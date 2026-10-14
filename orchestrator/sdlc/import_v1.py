"""Import v1's governance record of this repository into v2's audit trail, marked as decided by v1.

``python -m sdlc.import_v1 FILE [--apply]`` reads v1's export (``sdlc.v1_export``) and adds each
decision as an ``AgentDecision`` with source ``v1``, every field as v1 recorded it except a new
``id`` and ``supersedes_id`` remapped to the imported row. ``implementer`` and ``reviewer`` rows
(v2 records those runs itself) and trial rows are left out and counted. Each approval becomes one
``simulated_approver`` row; v2's live ``sdlc_approvals`` table is never touched. A row whose unique
key is already there is skipped, so a second import, or a newer export, adds only what is new.

It is a dry run unless ``--apply`` is given, and append-only (ADR 0004): it only inserts. The file
is checked before anything is written; on any error nothing is.
"""

import argparse
import sys
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import audit
from sdlc.db import get_engine
from sdlc.tables import AgentDecision
from sdlc.v1_export import DECISION_FIELDS, Approval, Decision, Export, ExportError, load_export

IMPORTED = "v1"
LEFT_OUT_AGENTS = {"implementer", "reviewer"}
APPROVER = "simulated_approver"
DRY_RUN = "Dry run: nothing was written. Add --apply to import."

Key = tuple[str, str, int, str | None, int]


@dataclass
class Tally:
    """One agent's rows: in the file, imported, skipped (already there) and left out."""

    in_file: int = 0
    imported: int = 0
    skipped: int = 0
    left_out: int = 0
    times: list[datetime] = field(default_factory=list)


@dataclass
class Plan:
    """What an import adds: rows in order, each with v1's id (``None`` for approvals)."""

    rows: list[tuple[int | None, AgentDecision]] = field(default_factory=list)
    existing: dict[int, int] = field(default_factory=dict)  # v1 id -> id of the row already there
    tallies: dict[str, Tally] = field(default_factory=dict)

    def tally(self, agent: str) -> Tally:
        return self.tallies.setdefault(agent, Tally())


def _key(row: AgentDecision) -> Key:
    return (row.agent, row.subject_type, row.subject_id, row.head_sha, row.attempt)


def _existing(db: Session) -> dict[Key, int]:
    query = select(AgentDecision).where(AgentDecision.subject_source == IMPORTED)
    return {_key(row): row.id for row in db.scalars(query)}


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment else None


def decision_row(decision: Decision) -> AgentDecision:
    """``decision`` as v1 recorded it, with source ``v1``; ``id`` and ``supersedes_id`` unset."""
    fields = {name: getattr(decision, name) for name in DECISION_FIELDS}
    del fields["id"], fields["supersedes_id"]
    return AgentDecision(**fields, subject_source=IMPORTED)


def approval_row(approval: Approval) -> AgentDecision:
    """One approval as a ``simulated_approver`` audit row."""
    if approval.requested_at is None:
        raise ExportError(f"The approval for PR {approval.pr} is missing 'requested_at'.")
    return AgentDecision(
        created_at=approval.decided_at or approval.requested_at,
        agent=APPROVER,
        agent_version=IMPORTED,
        subject_type="pr",
        subject_source=IMPORTED,
        subject_id=approval.pr,
        head_sha=f"approval-{approval.pr}",
        attempt=1,
        trigger="human",
        tier=approval.tier,
        status="ok" if approval.status == "approved" else "pending",
        output={
            "approver_id": approval.approver_id,
            "status": approval.status,
            "reason": approval.reason,
            "note": approval.note,
            "requested_at": _iso(approval.requested_at),
            "decided_at": _iso(approval.decided_at),
        },
    )


def _left_out(decision: Decision) -> bool:
    return decision.agent in LEFT_OUT_AGENTS or decision.trigger == audit.TRIAL


def plan_import(db: Session, export: Export) -> Plan:
    """What importing ``export`` would add, oldest first; it reads but never writes.

    Raises ``ExportError`` for an approval without ``requested_at``, before anything is planned."""
    approvals = [approval_row(approval) for approval in export.approvals]
    existing = _existing(db)
    plan = Plan()
    candidates: list[tuple[int | None, AgentDecision]] = []
    for decision in sorted(export.decisions, key=lambda d: (d.created_at, d.id)):
        tally = plan.tally(decision.agent)
        tally.in_file += 1
        tally.times.append(decision.created_at)
        if _left_out(decision):
            tally.left_out += 1
        else:
            candidates.append((decision.id, decision_row(decision)))
    for row in approvals:
        plan.tally(APPROVER).in_file += 1
        plan.tally(APPROVER).times.append(row.created_at)
    candidates += [(None, row) for row in approvals]

    seen: set[Key] = set()
    for v1_id, row in candidates:
        tally = plan.tally(row.agent)
        key = _key(row)
        if key in existing or key in seen:
            tally.skipped += 1
            if v1_id is not None and key in existing:
                plan.existing[v1_id] = existing[key]
            continue
        seen.add(key)
        tally.imported += 1
        plan.rows.append((v1_id, row))
    return plan


def apply(db: Session, export: Export, plan: Plan) -> int:
    """Insert the planned rows, remapping ``supersedes_id``, and commit once; returns the count.

    A ``supersedes_id`` whose target wasn't imported (left out, or not in the file) is ``None``."""
    supersedes = {d.id: d.supersedes_id for d in export.decisions}
    new_ids = dict(plan.existing)
    for v1_id, row in plan.rows:
        if v1_id is not None:
            row.supersedes_id = new_ids.get(supersedes[v1_id])
        db.add(row)
        db.flush()
        if v1_id is not None:
            new_ids[v1_id] = row.id
    db.commit()
    return len(plan.rows)


def _time(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else "none"


def format_report(plan: Plan, applied: bool) -> str:
    lines = [f"Importing v1's record as source {IMPORTED!r}:"]
    for agent, t in sorted(plan.tallies.items()):
        oldest, newest = (min(t.times), max(t.times)) if t.times else (None, None)
        lines.append(
            f"- {agent}: {t.in_file} in the file, {t.imported} imported, "
            f"{t.skipped} skipped (already there), {t.left_out} left out; "
            f"{_time(oldest)} to {_time(newest)}"
        )
    if any(t.left_out for t in plan.tallies.values()):
        lines.append(
            "Left out: implementer and reviewer rows (v2 records those runs itself) "
            "and trial rows."
        )
    if not applied:
        lines.append(DRY_RUN)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.import_v1", description="Import v1's decisions into the audit trail."
    )
    parser.add_argument("file", help="v1's export (python -m sdlc.export_rebuild --out FILE)")
    parser.add_argument("--apply", action="store_true", help="write the rows (a dry run otherwise)")
    args = parser.parse_args(argv)

    try:
        export = load_export(args.file)
    except ExportError as error:
        print(error, file=sys.stderr)
        return 1

    with Session(engine or get_engine()) as db:
        try:
            plan = plan_import(db, export)
        except ExportError as error:
            print(error, file=sys.stderr)
            return 1
        finally:
            db.rollback()
        if args.apply:
            apply(db, export, plan)

    print(format_report(plan, args.apply), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
