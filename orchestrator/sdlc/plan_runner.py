"""The planner runner: draft options once per slipping forecast of a real milestone, carefully.

For each open milestone whose latest saved forecast is slipping (``planner.is_slipping``) it calls
the planner agent once and records one audit row. A failed call is retried up to three times, at
least five minutes apart, and no milestone gets more than four calls a day (trial calls aside), so
a milestone whose items churn can't run up the bill. It writes only that audit row: never GitHub,
never an issue or a milestone, and it is never given a GitHub client.

``PLANNER_MODE`` (``off`` or ``on``, default ``off``) is its own switch, since it is the only part
of v2 that spends money. In the poll loop it also needs ``ORCHESTRATOR_MODE`` to be on; in the
collector it runs after the forecaster whenever ``PLANNER_MODE`` is ``on``.

``python -m sdlc.plan_runner dry-run [--milestone N]`` shows the exact request and a cost ceiling
without calling anything, ``try --yes [--milestone N]`` makes one real call and records only a
trial row, and ``once`` polls once. ``N`` is the milestone's M-number ("5" or "M5") or its title.
"""

import argparse
import json
import math
import sys
from datetime import datetime, time, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.agents import planner
from sdlc.agents.llm import cost_of
from sdlc.agents.pr_risk import CHARS_PER_TOKEN
from sdlc.audit import TRIAL, decisions_for, record_decision
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.epics import MILESTONE_TITLE, epic_label, milestone_order
from sdlc.forecast import EpicForecast, real_epics
from sdlc.forecaster import latest
from sdlc.tables import SOURCES, AgentDecision, Epic, Forecast

MAX_ATTEMPTS = 3
RETRY_AFTER = timedelta(minutes=5)
MAX_PER_DAY = 4  # calls per milestone per UTC day, failed ones included, trial calls aside


def as_epic_forecast(row: Forecast) -> EpicForecast:
    """The saved forecast as the planner takes it; the row keeps no closed count, so it is 0."""
    return EpicForecast(
        epic=row.subject,
        source=row.source,
        as_of=row.as_of,
        remaining_items=row.remaining_items,
        remaining_real=row.remaining_real,
        remaining_points=row.remaining_points,
        closed_items=0,
        end_date=row.end_date,
        p50=row.p50,
        p85=row.p85,
        on_time_probability=row.on_time_probability,
        throughput_mean=row.throughput_mean,
        history_days=row.history_days,
        runs=row.runs,
        seed=row.seed,
        ahead_items=row.ahead_items,
    )


def may_attempt(rows: list[AgentDecision], now: datetime) -> bool:
    """None tried yet, or fewer than the limit and the last at least five minutes ago."""
    if not rows:
        return True
    return len(rows) < MAX_ATTEMPTS and now - rows[-1].created_at >= RETRY_AFTER


def _milestone_rows(epic_name: str):
    """A query for v2's own planner rows (never v1's) about any forecast of ``epic_name``."""
    return (
        select(AgentDecision)
        .join(Forecast, Forecast.id == AgentDecision.subject_id)
        .where(
            AgentDecision.agent == planner.AGENT,
            AgentDecision.subject_type == "epic",
            AgentDecision.subject_source.in_(SOURCES),
            Forecast.kind == "epic",
            Forecast.subject == epic_name,
        )
    )


def calls_today(db: Session, epic_name: str, now: datetime) -> int:
    """The non-trial planner calls about ``epic_name`` since midnight UTC, failed ones included."""
    midnight = datetime.combine(now.date(), time())
    query = _milestone_rows(epic_name).where(
        AgentDecision.trigger != TRIAL, AgentDecision.created_at >= midnight
    )
    return db.scalar(select(func.count()).select_from(query.subquery()))


def _subject(row: Forecast) -> dict[str, Any]:
    return {"subject_type": "epic", "subject_source": row.source, "subject_id": row.id}


def to_decision_fields(proposal: planner.Proposal, row: Forecast, model: str) -> dict[str, Any]:
    """The keyword arguments ``record_decision`` needs for ``proposal``, besides the trigger."""
    llm = proposal.llm
    return {
        "agent": planner.AGENT,
        "agent_version": planner.AGENT_VERSION,
        **_subject(row),
        "head_sha": row.inputs_hash[:40],
        "model_id": llm.model if llm else model,
        "prompt_version": planner.PROMPT_VERSION,
        "prompt_hash": planner.PROMPT_HASH,
        "output": {
            "summary": proposal.summary,
            "options": proposal.options,
            "recommended": proposal.recommended,
            "confidence": proposal.confidence,
            "dropped": proposal.dropped,
            "milestone": epic_label(row.subject),
            "forecast_id": row.id,
            "cost_usd": llm.cost_usd if llm else None,
        },
        "status": proposal.status,
        "error": proposal.error,
        "latency_ms": llm.latency_ms if llm else None,
        "input_tokens": llm.input_tokens if llm else None,
        "output_tokens": llm.output_tokens if llm else None,
    }


class PlannerRunner:
    def __init__(self, llm: Any, mode: str, *, engine: Engine | None = None):
        self.llm = llm
        self.mode = mode
        self.engine = engine

    def poll_once(self, now: datetime | None = None) -> dict[str, Any]:
        """Draft options for each slipping milestone not yet drafted, within the caps."""
        if self.mode != "on":
            return {"mode": self.mode}
        now = now or utcnow()
        summary = {"mode": self.mode, "slipping": 0, "proposed": 0, "failed": 0, "skipped": 0}
        with Session(self.engine or get_engine()) as db:
            for epic in real_epics(db):
                row = latest(db, "epic", epic.name)
                if row is None or not planner.is_slipping(as_epic_forecast(row)):
                    continue
                summary["slipping"] += 1
                rows = decisions_for(
                    db, planner.AGENT, head_sha=row.inputs_hash[:40], **_subject(row)
                )
                if (
                    any(done.status == "ok" for done in rows)
                    or not may_attempt(rows, now)
                    or calls_today(db, epic.name, now) >= MAX_PER_DAY
                ):
                    summary["skipped"] += 1
                    continue
                proposal = planner.propose(db, epic, as_epic_forecast(row), llm=self.llm)
                record_decision(
                    db,
                    trigger="schedule",
                    now=now,
                    attempt=len(rows) + 1,
                    **to_decision_fields(proposal, row, self.llm.model),
                )
                db.commit()
                summary["proposed" if proposal.ok else "failed"] += 1
        return summary


def _iso(moment) -> str | None:
    return moment.isoformat() if moment else None


def latest_proposal(db: Session, epic_name: str) -> dict[str, Any] | None:
    """The newest successful, non-trial proposal for ``epic_name``'s forecasts, or None.

    ``current`` says whether it was drafted from the milestone's latest forecast.
    """
    decision = db.scalars(
        _milestone_rows(epic_name)
        .where(AgentDecision.status == "ok", AgentDecision.trigger != TRIAL)
        .order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
        .limit(1)
    ).first()
    if decision is None:
        return None
    output = decision.output or {}
    newest = latest(db, "epic", epic_name)
    return {
        "summary": output.get("summary") or "",
        "options": output.get("options") or [],
        "recommended": output.get("recommended") or 0,
        "confidence": output.get("confidence"),
        "created_at": _iso(decision.created_at),
        "forecast_id": decision.subject_id,
        "current": newest is not None and newest.id == decision.subject_id,
    }


def find_milestone(db: Session, value: str) -> Epic | None:
    """The open milestone whose M-number is ``value`` ("5" or "M5"), or whose title it is."""
    wanted = value.strip()
    number = wanted.upper() if wanted.upper().startswith("M") else f"M{wanted}"
    for epic in real_epics(db):
        match = MILESTONE_TITLE.match(epic.name.strip())
        if (match and match.group(1).upper() == number) or epic.name.strip() == wanted:
            return epic
    return None


def _target(db: Session, value: str | None) -> tuple[Epic, Forecast] | str:
    """The named milestone and its latest forecast, or the first slipping one; else a message."""
    if value is not None:
        epic = find_milestone(db, value)
        if epic is None:
            return f"No open milestone matches {value!r}."
        row = latest(db, "epic", epic.name)
        if row is None:
            return f"{epic.name} has no saved forecast yet."
        return epic, row
    for epic in sorted(real_epics(db), key=lambda epic: milestone_order(epic.name)):
        row = latest(db, "epic", epic.name)
        if row is not None and planner.is_slipping(as_epic_forecast(row)):
            return epic, row
    return "No open milestone is slipping."


def dry_run(db: Session, epic: Epic, row: Forecast, *, llm: Any) -> dict[str, Any]:
    """The exact request ``propose`` would send and its cost ceiling, without calling anything."""
    forecast = as_epic_forecast(row)
    items = planner.milestone_items(db, epic.name)
    user = planner.build_prompt(forecast, items, planner.next_milestone(db, epic))
    request = llm.request_kwargs(planner.SYSTEM_PROMPT, user, planner.SCHEMA, "medium")
    prompt_chars = len(planner.SYSTEM_PROMPT) + len(user) + len(json.dumps(planner.SCHEMA))
    prompt_tokens = math.ceil(prompt_chars / CHARS_PER_TOKEN)
    return {
        "milestone": epic.name,
        "forecast_id": row.id,
        "slipping": planner.is_slipping(forecast),
        "request": request,
        "max_cost_usd": cost_of(llm.model, prompt_tokens, llm.max_tokens, 0, 0),
    }


def _try(db: Session, epic: Epic, row: Forecast, *, llm: Any) -> int:
    proposal = planner.propose(db, epic, as_epic_forecast(row), llm=llm)
    fields = to_decision_fields(proposal, row, llm.model)
    highest = db.scalar(
        select(func.max(AgentDecision.attempt)).where(
            AgentDecision.agent == planner.AGENT,
            AgentDecision.subject_type == "epic",
            AgentDecision.subject_source == row.source,
            AgentDecision.subject_id == row.id,
            AgentDecision.head_sha == fields["head_sha"],
        )
    )
    decision = record_decision(
        db,
        trigger=TRIAL,
        attempt=max(highest or 0, MAX_ATTEMPTS) + 1,  # clear of the poll's attempts
        **fields,
    )
    db.commit()
    print(f"{epic.name}, forecast {row.id}: {proposal.status}")
    if proposal.summary:
        print(proposal.summary)
    for index, option in enumerate(proposal.options):
        mark = "*" if index == proposal.recommended else "-"
        items = ", ".join(f"#{number}" for number in option["items"])
        print(f"  {mark} {option['action']} {items}: {option['title']}")
        print(f"    {option['rationale']}")
        if effect := option["effect"]:
            before = effect["from"]
            print(f"    P50 {before['p50']} -> {effect['p50']}")
            print(f"    P85 {before['p85']} -> {effect['p85']}")
            print(f"    On time {before['on_time_probability']} -> {effect['on_time_probability']}")
    if proposal.confidence is not None:
        print(f"Confidence: {proposal.confidence}")
    for line in proposal.dropped:
        print(f"Dropped: {line}")
    if proposal.error:
        print(f"Error: {proposal.error}")
    cost = proposal.llm.cost_usd if proposal.llm else None
    print(
        f"Recorded as trial decision {decision.id}; cost {cost} USD. "
        "Nothing was written to GitHub."
    )
    return 0


def main(argv: list[str] | None = None, engine: Engine | None = None, llm: Any = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.plan_runner", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("once", help="poll once and print the summary")
    dry = commands.add_parser("dry-run", help="show the request and its cost; call nothing")
    dry.add_argument("--milestone", metavar="N", help='an M-number ("5" or "M5") or a title')
    trial = commands.add_parser("try", help="make one real call; record only a trial row")
    trial.add_argument("--milestone", metavar="N", help='an M-number ("5" or "M5") or a title')
    trial.add_argument("--yes", action="store_true", help="confirm the real, paid call")
    args = parser.parse_args(argv)

    if args.command == "try" and not args.yes:
        print("This makes one real, paid model call. Run it again with --yes to go ahead.")
        return 1
    llm = llm or planner.planner_llm()
    if args.command == "once":
        runner = PlannerRunner(llm, get_settings().planner_mode, engine=engine)
        print(json.dumps(runner.poll_once()))
        return 0
    with Session(engine or get_engine()) as db:
        target = _target(db, args.milestone)
        if isinstance(target, str):
            print(target)
            return 1
        epic, row = target
        if args.command == "dry-run":
            result = dry_run(db, epic, row, llm=llm)
            db.rollback()  # a dry run writes nothing
            print(json.dumps(result, indent=2))
            return 0
        return _try(db, epic, row, llm=llm)


if __name__ == "__main__":
    sys.exit(main())
