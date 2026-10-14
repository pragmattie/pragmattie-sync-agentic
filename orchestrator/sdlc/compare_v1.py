"""Compare v2's decisions with v1's on the same PRs, issues and deploys, and report the differences.

``python -m sdlc.compare_v1 FILE [--since DATE] [--markdown OUT]`` pairs v1's rows from its export
(``sdlc.v1_export``) with v2's own rows (source ``github``, trial rows left out) from the day v2
started observing (its first non-trial ``pr_risk`` row) or ``--since``. Per agent it counts the
subjects both sides decided, how often they agree, and lists every difference with what caused it;
rows only one side has are counted, not compared. It ends with the bar for cutover, fixed on
2026-10-14 before any comparison was run. It is read-only: one query, then a rollback.
"""

import argparse
import sys
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import audit
from sdlc.agents import pr_risk, release_gate, suite_select, triage
from sdlc.db import get_engine
from sdlc.suite_selector import RESULT
from sdlc.tables import AgentDecision
from sdlc.tiers import TIER_IDS
from sdlc.v1_export import Export, ExportError, load_export

SOURCE = "github"
DIMENSIONS = ("module", "type", "priority", "points")

# The bar for cutover, fixed on 2026-10-14 before any comparison was run.
BAR_FIXED_ON = "2026-10-14"
BAR_TIER_AGREEMENT = 0.90
BAR_FLOORED_LOWER = 0  # commits where v2's tier is below v1's on a PR v1 floored
BAR_VERDICT_AGREEMENT = 1.0

NOT_OBSERVING = "v2 wasn't observing yet"
NOT_COMPARED = (
    "`/tier` rulings and objection-window sign-offs: v2 posts nothing while it observes, "
    "so they can't match yet."
)


@dataclass(frozen=True)
class Row:
    """One decision from either side, with only what the comparison reads."""

    id: int
    created_at: datetime
    agent: str
    subject_type: str
    subject_id: int
    head_sha: str | None
    trigger: str
    status: str
    final_score: int | None = None
    tier: str | None = None
    signals: dict | None = None
    inputs_digest: dict | None = None
    output: dict | None = None

    @property
    def key(self) -> tuple[int, str | None]:
        return self.subject_id, self.head_sha


@dataclass
class OneSided:
    """Subjects only one side decided, counted by side and reason."""

    counts: dict[tuple[str, str | None], int] = field(default_factory=dict)

    def add(self, side: str, reason: str | None) -> None:
        self.counts[side, reason] = self.counts.get((side, reason), 0) + 1


@dataclass
class Comparison:
    """One agent's comparison: what both decided, how often they agree, and every difference."""

    agent: str
    unit: str
    compared: int = 0
    agreed: dict[str, int] = field(default_factory=dict)
    differences: list[str] = field(default_factory=list)
    one_sided: OneSided = field(default_factory=OneSided)
    bugs: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Bar:
    tier_agreement: float | None
    floored_lower: tuple[str, ...]
    verdict_agreement: float | None

    @property
    def tiers_met(self) -> bool:
        return self.tier_agreement is not None and self.tier_agreement >= BAR_TIER_AGREEMENT

    @property
    def floors_met(self) -> bool:
        return len(self.floored_lower) <= BAR_FLOORED_LOWER

    @property
    def verdicts_met(self) -> bool:
        return self.verdict_agreement is None or self.verdict_agreement >= BAR_VERDICT_AGREEMENT

    @property
    def met(self) -> bool:
        return self.tiers_met and self.floors_met and self.verdicts_met


@dataclass(frozen=True)
class Report:
    start: datetime | None
    end: datetime | None
    observing_since: datetime | None
    risk: Comparison
    suites: Comparison
    triage: Comparison
    gate: Comparison
    bar: Bar
    not_compared: dict[str, int]


def v1_rows(export: Export) -> list[Row]:
    return [
        Row(
            id=d.id,
            created_at=d.created_at,
            agent=d.agent,
            subject_type=d.subject_type,
            subject_id=d.subject_id,
            head_sha=d.head_sha,
            trigger=d.trigger,
            status=d.status,
            final_score=d.final_score,
            tier=d.tier,
            signals=d.signals,
            inputs_digest=d.inputs_digest,
            output=d.output,
        )
        for d in export.decisions
        if d.trigger != audit.TRIAL
    ]


def _row(d: AgentDecision) -> Row:
    return Row(
        id=d.id,
        created_at=d.created_at,
        agent=d.agent,
        subject_type=d.subject_type,
        subject_id=d.subject_id,
        head_sha=d.head_sha,
        trigger=d.trigger,
        status=d.status,
        final_score=d.final_score,
        tier=d.tier,
        signals=d.signals,
        inputs_digest=d.inputs_digest,
        output=d.output,
    )


def observing_since(db: Session) -> datetime | None:
    """When v2 started observing: its first non-trial ``pr_risk`` row on real work."""
    return db.scalar(
        select(AgentDecision.created_at)
        .where(
            AgentDecision.agent == pr_risk.AGENT,
            AgentDecision.subject_source == SOURCE,
            AgentDecision.trigger != audit.TRIAL,
        )
        .order_by(AgentDecision.created_at, AgentDecision.id)
        .limit(1)
    )


def v2_rows(db: Session, start: datetime | None) -> list[Row]:
    """v2's own non-trial rows on real work from ``start`` on, oldest first."""
    query = select(AgentDecision).where(
        AgentDecision.subject_source == SOURCE, AgentDecision.trigger != audit.TRIAL
    )
    if start is not None:
        query = query.where(AgentDecision.created_at >= start)
    return [_row(d) for d in db.scalars(query.order_by(AgentDecision.created_at, AgentDecision.id))]


def _of(rows: Iterable[Row], agent: str, subject_type: str) -> list[Row]:
    return [r for r in rows if r.agent == agent and r.subject_type == subject_type]


def _newest(rows: Iterable[Row], ok_only: bool = True) -> dict[tuple, Row]:
    """The newest row (``ok`` ones only, by default) for each subject and version."""
    newest: dict[tuple, Row] = {}
    for row in sorted(rows, key=lambda r: (r.created_at, r.id)):
        if not ok_only or row.status == "ok":
            newest[row.key] = row
    return newest


def _pairs(
    comparison: Comparison,
    v1: list[Row],
    v2: list[Row],
    observing: datetime | None,
    ok_only: bool = True,
) -> list[tuple[tuple, Row, Row]]:
    """The subjects both sides decided, paired; the rest counted on ``comparison`` with a reason."""
    new1, new2 = _newest(v1, ok_only), _newest(v2, ok_only)
    keys1, keys2 = {r.key for r in v1}, {r.key for r in v2}
    for key in sorted(keys1 | keys2, key=str):
        if key in new1 and key in new2:
            continue
        if key not in new1 and key not in new2:  # every run failed, on one side or both
            side = "v1" if key not in keys2 else "v2" if key not in keys1 else "both"
            comparison.one_sided.add(side, "every run failed")
            continue
        side, other, other_keys = ("v1", "v2", keys2) if key in new1 else ("v2", "v1", keys1)
        reason = None
        if key in other_keys:
            reason = f"{other}'s run failed"
        elif side == "v1" and (observing is None or new1[key].created_at < observing):
            reason = NOT_OBSERVING
        comparison.one_sided.add(side, reason)
    pairs = [(key, new1[key], new2[key]) for key in new1 if key in new2]
    comparison.compared = len(pairs)
    return sorted(pairs, key=lambda pair: str(pair[0]))


def _label(kind: str, key: tuple) -> str:
    number, sha = key
    if kind == "release":
        return f"deploy {(sha or '')[:7]}"
    if kind == "issue":
        return f"issue #{number} ({(sha or '')[:12]})"
    return f"PR #{number} @ {(sha or '')[:7]}"


def _dict_diff(a: dict | None, b: dict | None) -> list[str]:
    a, b = a or {}, b or {}
    return [
        f"{name} {a.get(name)!r} vs {b.get(name)!r}"
        for name in sorted(set(a) | set(b))
        if a.get(name) != b.get(name)
    ]


def _floors(row: Row) -> list:
    return list((row.output or {}).get("floors") or [])


def _rank(tier: str | None) -> int:
    return TIER_IDS.index(tier) if tier in TIER_IDS else -1


def compare_risk(
    v1: list[Row], v2: list[Row], observing: datetime | None
) -> tuple[Comparison, list[str]]:
    """PR risk per (PR, commit); also the commits where v2's tier is below v1's on a floored PR."""
    result = Comparison(pr_risk.AGENT, "commits")
    pairs = _pairs(result, _of(v1, pr_risk.AGENT, "pr"), _of(v2, pr_risk.AGENT, "pr"), observing)
    lower: list[str] = []
    agreed = 0
    for key, a, b in pairs:
        label = _label("pr", key)
        floors = _floors(a) or _floors(b)
        if floors and _rank(b.tier) < _rank(a.tier):
            lower.append(f"{label}: v1 {a.tier}, v2 {b.tier} (floors: {', '.join(floors)})")
        if a.tier == b.tier:
            agreed += 1
            if a.final_score == b.final_score:
                continue
        causes = _dict_diff(a.signals, b.signals)
        causes += [f"input {item}" for item in _dict_diff(a.inputs_digest, b.inputs_digest)]
        same = " (same tier)" if a.tier == b.tier else ""
        result.differences.append(
            f"{label}: v1 {a.tier} score {a.final_score}, v2 {b.tier} score {b.final_score}{same}"
            f"; differs in: {'; '.join(causes) or 'no recorded signal'}"
        )
    result.agreed = {"tier": agreed}
    return result, lower


def compare_suites(v1: list[Row], v2: list[Row], observing: datetime | None) -> Comparison:
    """The test selector per (PR, commit), and CI results where both sides settled the commit."""
    result = Comparison(suite_select.AGENT, "commits")
    recs1 = [r for r in _of(v1, suite_select.AGENT, "pr") if r.trigger != RESULT]
    recs2 = [r for r in _of(v2, suite_select.AGENT, "pr") if r.trigger != RESULT]
    agreed = 0
    for key, a, b in _pairs(result, recs1, recs2, observing, ok_only=False):
        out1, out2 = a.output or {}, b.output or {}
        suites1, suites2 = set(out1.get("suites") or []), set(out2.get("suites") or [])
        if suites1 == suites2:
            agreed += 1
            continue
        why1, why2 = out1.get("reasons") or {}, out2.get("reasons") or {}
        parts = [f"v1 only {s} ({why1.get(s, 'no reason')})" for s in sorted(suites1 - suites2)]
        parts += [f"v2 only {s} ({why2.get(s, 'no reason')})" for s in sorted(suites2 - suites1)]
        if out1.get("full") or out2.get("full"):
            parts.append(f"every job: v1 {out1.get('full')!r}, v2 {out2.get('full')!r}")
        result.differences.append(f"{_label('pr', key)}: {'; '.join(parts)}")
    result.agreed = {"suites": agreed}

    settled1 = _newest([r for r in _of(v1, suite_select.AGENT, "pr") if r.trigger == RESULT], False)
    settled2 = _newest([r for r in _of(v2, suite_select.AGENT, "pr") if r.trigger == RESULT], False)
    for key in sorted(set(settled1) & set(settled2), key=str):
        res1 = (settled1[key].output or {}).get("results")
        res2 = (settled2[key].output or {}).get("results")
        if res1 != res2:
            result.bugs.append(
                f"{_label('pr', key)}: CI results differ ({'; '.join(_dict_diff(res1, res2))})"
            )
    return result


def _dimension(row: Row, name: str):
    output = row.output or {}
    return output.get("estimate_points") if name == "points" else output.get(name)


def compare_triage(v1: list[Row], v2: list[Row], observing: datetime | None) -> Comparison:
    """Triage per (issue, content hash), dimension by dimension."""
    result = Comparison(triage.AGENT, "issue versions")
    agreed = dict.fromkeys(DIMENSIONS, 0)
    rows1, rows2 = _of(v1, triage.AGENT, "issue"), _of(v2, triage.AGENT, "issue")
    for key, a, b in _pairs(result, rows1, rows2, observing):
        parts = []
        for name in DIMENSIONS:
            first, second = _dimension(a, name), _dimension(b, name)
            if first == second:
                agreed[name] += 1
            else:
                parts.append(f"{name} {first!r} vs {second!r}")
        if parts:
            result.differences.append(f"{_label('issue', key)}: {'; '.join(parts)}")
    result.agreed = agreed
    return result


def compare_gate(v1: list[Row], v2: list[Row], observing: datetime | None) -> Comparison:
    """The release gate per deploy commit: each side's final verdict and tier."""
    result = Comparison(release_gate.AGENT, "deploys")
    # A deploy is keyed by its commit alone: the PR number beside it may differ between sides.
    gate1 = [replace(r, subject_id=0) for r in _of(v1, release_gate.AGENT, "release")]
    gate2 = [replace(r, subject_id=0) for r in _of(v2, release_gate.AGENT, "release")]
    agreed = 0
    for key, a, b in _pairs(result, gate1, gate2, observing):
        verdict1, verdict2 = (a.output or {}).get("verdict"), (b.output or {}).get("verdict")
        if verdict1 == verdict2:
            agreed += 1
        if verdict1 != verdict2 or a.tier != b.tier:
            reasons1 = "; ".join((a.output or {}).get("reasons") or []) or "no reasons"
            reasons2 = "; ".join((b.output or {}).get("reasons") or []) or "no reasons"
            result.differences.append(
                f"{_label('release', key)}: v1 {verdict1} at {a.tier} ({reasons1}), "
                f"v2 {verdict2} at {b.tier} ({reasons2})"
            )
    result.agreed = {"verdict": agreed}
    return result


def _share(agreed: int, compared: int) -> float | None:
    return agreed / compared if compared else None


def compare(
    v1: list[Row],
    v2: list[Row],
    *,
    start: datetime | None,
    observing: datetime | None,
    not_compared: dict[str, int] | None = None,
) -> Report:
    """Every comparison and the bar, over v1's rows from ``start`` on and v2's rows as given."""
    v1 = [r for r in v1 if start is None or r.created_at >= start]
    risk, lower = compare_risk(v1, v2, observing)
    gate = compare_gate(v1, v2, observing)
    times = [r.created_at for r in (*v1, *v2)]
    return Report(
        start=start or (min(times) if times else None),
        end=max(times) if times else None,
        observing_since=observing,
        risk=risk,
        suites=compare_suites(v1, v2, observing),
        triage=compare_triage(v1, v2, observing),
        gate=gate,
        bar=Bar(
            tier_agreement=_share(risk.agreed["tier"], risk.compared),
            floored_lower=tuple(lower),
            verdict_agreement=_share(gate.agreed["verdict"], gate.compared),
        ),
        not_compared=not_compared or {},
    )


def _time(moment: datetime | None) -> str:
    return moment.strftime("%Y-%m-%d %H:%M UTC") if moment else "none"


def _percent(agreed: int, compared: int) -> str:
    share = _share(agreed, compared)
    return f"{agreed} of {compared} ({share:.0%})" if share is not None else f"{agreed} of 0"


def _section(title: str, c: Comparison) -> list[str]:
    lines = [f"## {title} (`{c.agent}`)", "", f"- {c.unit.capitalize()} compared: {c.compared}"]
    for name, agreed in c.agreed.items():
        lines.append(f"- Agreement on {name}: {_percent(agreed, c.compared)}")
    for (side, reason), count in sorted(c.one_sided.counts.items(), key=str):
        where = "on both sides" if side == "both" else f"{side} only"
        because = f" ({reason})" if reason else ""
        lines.append(f"- Not compared, {where}: {count} {c.unit}{because}")
    lines += ["", f"Differences: {len(c.differences)}"]
    lines += [f"- {difference}" for difference in c.differences]
    if c.bugs:
        lines += ["", f"Bugs (CI results should match exactly): {len(c.bugs)}"]
        lines += [f"- {bug}" for bug in c.bugs]
    return [*lines, ""]


def _mark(met: bool) -> str:
    return "met" if met else "not met"


def format_report(report: Report) -> str:
    bar = report.bar
    tiers = (
        f"{bar.tier_agreement:.0%}" if bar.tier_agreement is not None else "no commits both scored"
    )
    verdicts = (
        f"{bar.verdict_agreement:.0%}"
        if bar.verdict_agreement is not None
        else "no deploys both judged"
    )
    lines = [
        "# v1 and v2 side by side",
        "",
        f"Period: {_time(report.start)} to {_time(report.end)}",
        f"v2 observing since: {_time(report.observing_since)}",
        "",
        *_section("PR risk", report.risk),
        *_section("Test selector", report.suites),
        *_section("Triage", report.triage),
        *_section("Release gate", report.gate),
        "## Not compared",
        "",
        f"- {NOT_COMPARED}",
    ]
    lines += [f"- v1 {what}: {count}" for what, count in sorted(report.not_compared.items())]
    lines += [
        "",
        f"## The bar for cutover (fixed {BAR_FIXED_ON}): {_mark(bar.met)}",
        "",
        f"- Tiers agree on at least {BAR_TIER_AGREEMENT:.0%} of commits both scored: "
        f"{tiers}, {_mark(bar.tiers_met)}",
        f"- No commit where v2's tier is lower than v1's on a PR v1 floored: "
        f"{len(bar.floored_lower)}, {_mark(bar.floors_met)}",
    ]
    lines += [f"  - {item}" for item in bar.floored_lower]
    lines += [
        f"- Release verdicts agree on every deploy both judged: {verdicts}, "
        f"{_mark(bar.verdicts_met)}",
        "",
        "An aid to the cutover decision, which is made at 7.5.",
    ]
    return "\n".join(lines) + "\n"


def _not_compared(export: Export) -> dict[str, int]:
    rulings = sum(1 for d in export.decisions if d.agent == "tier_override")
    windows = sum(1 for d in export.decisions if d.agent == "objection_window")
    return {
        "`/tier` rulings": rulings,
        "objection-window sign-offs": windows,
        "approvals": len(export.approvals),
    }


def _date(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"not a date: {value}") from error


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.compare_v1", description="Compare v2's decisions with v1's."
    )
    parser.add_argument("file", help="v1's export (python -m sdlc.export_rebuild --out FILE)")
    parser.add_argument("--since", type=_date, help="compare from this date (UTC)")
    parser.add_argument("--markdown", metavar="OUT", help="also write the report to this file")
    args = parser.parse_args(argv)

    try:
        export = load_export(args.file)
    except ExportError as error:
        print(error, file=sys.stderr)
        return 1

    with Session(engine or get_engine()) as db:
        try:
            observing = observing_since(db)
            start = args.since or observing
            rows = v2_rows(db, start)
        finally:
            db.rollback()

    report = compare(
        v1_rows(export),
        rows,
        start=start,
        observing=observing,
        not_compared=_not_compared(export),
    )
    text = format_report(report)
    print(text, end="")
    if args.markdown:
        Path(args.markdown).write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
