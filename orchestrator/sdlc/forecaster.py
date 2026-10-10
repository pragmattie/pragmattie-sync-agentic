"""The forecaster agent: save a fresh forecast only when something it depends on has changed.

Its subjects are the current simulated sprint and every epic: each open GitHub milestone (real
work) and each simulated epic. For each it fingerprints the inputs (the day and the open items;
for a sprint each item's id, points, module and first pull request, for an epic each story's id,
points and source plus the epic's due date), and saves a forecast with ``sdlc.forecast`` when the
fingerprint differs from the latest saved one: a new day, an item added, closed or re-estimated,
work starting, or a target date moved. Each save is one ``sdlc_forecasts`` row and one audit row.
It makes no model call and no GitHub request, so it costs nothing to run.

It runs after the PR risk and triage agents in ``python -m sdlc.runner run``, where
``ORCHESTRATOR_MODE=off`` stops it, and after each collection in the collector, where it always
runs: it writes only its own two tables.

``python -m sdlc.forecaster once`` polls once; ``now`` saves a fresh forecast of everything.
"""

import argparse
import dataclasses
import hashlib
import json
import sys
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.audit import record_decision
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.epics import MILESTONE_TITLE, epic_label
from sdlc.forecast import (
    RUNS,
    EpicForecast,
    SprintForecast,
    current_sprint,
    describe,
    describe_epic,
    epic_forecast,
    real_epic,
    real_epics,
    simulated_epics,
    sprint_forecast,
)
from sdlc.tables import AgentDecision, Forecast, Issue, PullRequest, Sprint

AGENT = "forecaster"
AGENT_VERSION = "v1"
ALWAYS_ON = "enforce"  # the collector's mode: it has no GitHub effects for shadow to hold back
TRAIL = 30
SOURCE = "synthetic"
REAL_NOTE = "Real build: forecast at the agents' pace"
SIMULATED_NOTE = "Simulated history: calibration data"


def _fingerprint(today: date, subject: str, rows: list[dict[str, Any]]) -> str:
    """sha256 of the day, the subject and the rows, sorted as JSON."""
    ordered = sorted(json.dumps(row, sort_keys=True) for row in rows)
    payload = json.dumps({"day": today.isoformat(), "subject": subject, "rows": ordered})
    return hashlib.sha256(payload.encode()).hexdigest()


def sprint_inputs(db: Session, sprint: Sprint) -> list[dict[str, Any]]:
    """Each open item in ``sprint``: its id, points, module and first pull request's time."""
    first_pr = (
        select(PullRequest.created_at)
        .where(PullRequest.issue_id == Issue.id)
        .order_by(PullRequest.created_at)
        .limit(1)
        .scalar_subquery()
    )
    rows = db.execute(
        select(Issue.id, Issue.estimate_points, Issue.module, first_pr).where(
            Issue.sprint_id == sprint.id, Issue.state == "open"
        )
    ).all()
    return [
        {
            "id": issue_id,
            "points": points,
            "module": module,
            "started": started.isoformat() if started else None,
        }
        for issue_id, points, module, started in rows
    ]


def epic_inputs(db: Session, epic: str) -> list[dict[str, Any]]:
    """Each open story of ``epic`` (its id, points and source), and the epic's due date."""
    rows = db.execute(
        select(Issue.id, Issue.estimate_points, Issue.source).where(
            Issue.epic == epic, Issue.state == "open"
        )
    ).all()
    milestone = real_epic(db, epic)
    due_on = milestone.due_on if milestone else None
    return [
        {"id": issue_id, "points": points, "source": source} for issue_id, points, source in rows
    ] + [{"due_on": _iso(due_on)}]


def latest(db: Session, kind: str, subject: str) -> Forecast | None:
    """The newest saved forecast of ``subject``."""
    return db.scalars(
        select(Forecast)
        .where(Forecast.kind == kind, Forecast.subject == subject)
        .order_by(Forecast.created_at.desc(), Forecast.id.desc())
        .limit(1)
    ).first()


def _risk(risk) -> dict[str, Any]:
    data = dataclasses.asdict(risk)
    data["started"] = risk.started.isoformat() if risk.started else None
    return data


def _save(
    db: Session,
    forecast: SprintForecast | EpicForecast,
    *,
    kind: str,
    inputs_hash: str,
    trigger: str,
    now: datetime,
) -> Forecast:
    """Write one forecast row and the audit row that records it; flushes, never commits."""
    if isinstance(forecast, SprintForecast):
        subject, remaining_real = forecast.sprint, 0
        at_risk, rationale = [_risk(risk) for risk in forecast.at_risk], describe(forecast)
    else:
        subject, remaining_real = forecast.epic, forecast.remaining_real
        at_risk, rationale = [], describe_epic(forecast)
    row = Forecast(
        created_at=now.replace(microsecond=0),
        as_of=forecast.as_of,
        kind=kind,
        subject=subject,
        source=forecast.source,
        trigger=trigger,
        inputs_hash=inputs_hash,
        remaining_items=forecast.remaining_items,
        remaining_real=remaining_real,
        remaining_points=forecast.remaining_points,
        end_date=forecast.end_date,
        p50=forecast.p50,
        p85=forecast.p85,
        on_time_probability=forecast.on_time_probability,
        throughput_mean=forecast.throughput_mean,
        history_days=forecast.history_days,
        runs=forecast.runs,
        seed=forecast.seed,
        at_risk=at_risk,
    )
    db.add(row)
    db.flush()
    record_decision(
        db,
        agent=AGENT,
        agent_version=AGENT_VERSION,
        subject_type=kind,
        subject_source=row.source,
        subject_id=row.id,
        trigger=trigger,
        now=now,
        head_sha=inputs_hash[:40],
        inputs_digest={
            "remaining_items": row.remaining_items,
            "remaining_points": row.remaining_points,
            "throughput_mean": row.throughput_mean,
            "history_days": row.history_days,
        },
        output={
            "subject": row.subject,
            "p50": _iso(row.p50),
            "p85": _iso(row.p85),
            "end_date": _iso(row.end_date),
            "confidence": row.on_time_probability,
            "rationale": rationale,
            "runs": row.runs,
            "seed": row.seed,
        },
        action_taken={"saved_forecast": row.id},
        status="ok",
    )
    return row


class ForecastRunner:
    def __init__(self, mode: str, *, engine: Engine | None = None, runs: int = RUNS):
        self.mode = mode
        self.engine = engine
        self.runs = runs

    def poll_once(self, now: datetime | None = None, *, force: bool = False) -> dict[str, Any]:
        """Save a forecast of each subject whose inputs changed, or of every one with ``force``."""
        if self.mode == "off":
            return {"mode": "off"}
        now = now or utcnow()
        today = now.date()
        summary = {"mode": self.mode, "assessed": 0, "unchanged": 0}
        with Session(self.engine or get_engine()) as db:
            sprint = current_sprint(db, today, SOURCE)
            if sprint is not None:
                self._poll_subject(
                    db,
                    summary,
                    kind="sprint",
                    subject=sprint.name,
                    rows=sprint_inputs(db, sprint),
                    build=lambda: sprint_forecast(db, today, sprint=sprint, runs=self.runs),
                    now=now,
                    force=force,
                )
            real = [epic.name for epic in real_epics(db)]
            simulated = [name for name in simulated_epics(db) if name not in real]
            for name in real + simulated:
                self._poll_subject(
                    db,
                    summary,
                    kind="epic",
                    subject=name,
                    rows=epic_inputs(db, name),
                    build=lambda name=name: epic_forecast(db, today, name, runs=self.runs),
                    now=now,
                    force=force,
                )
        return summary

    def _poll_subject(
        self,
        db: Session,
        summary: dict[str, Any],
        *,
        kind: str,
        subject: str,
        rows: list[dict[str, Any]],
        build: Callable[[], SprintForecast | EpicForecast],
        now: datetime,
        force: bool,
    ) -> None:
        """Save a forecast of ``subject`` if its fingerprint changed, or with ``force``."""
        today = now.date()
        inputs_hash = _fingerprint(today, subject, rows)
        previous = latest(db, kind, subject)
        if not force and previous is not None and previous.inputs_hash == inputs_hash:
            summary["unchanged"] += 1
            return
        if force:
            trigger = "manual"
        elif previous is None or previous.as_of < today:
            trigger = "schedule"
        else:
            trigger = "change"
        _save(db, build(), kind=kind, inputs_hash=inputs_hash, trigger=trigger, now=now)
        db.commit()
        summary["assessed"] += 1


def _iso(day: date | datetime | None) -> str | None:
    return day.isoformat() if day else None


def serialize(row: Forecast) -> dict[str, Any]:
    """Every column as a JSON-safe value, dates and times as ISO text."""
    data = {column.key: getattr(row, column.key) for column in Forecast.__table__.columns}
    for key in ("created_at", "as_of", "end_date", "p50", "p85"):
        data[key] = _iso(data[key])
    return data


def _history(db: Session, kind: str, subject: str, limit: int) -> list[Forecast]:
    """The newest ``limit`` forecasts of ``subject``, newest first."""
    return list(
        db.scalars(
            select(Forecast)
            .where(Forecast.kind == kind, Forecast.subject == subject)
            .order_by(Forecast.created_at.desc(), Forecast.id.desc())
            .limit(limit)
        )
    )


def _days_moved(new: date | None, old: date | None) -> int | None:
    return (new - old).days if new and old else None


MOVED_KEYS = ("p50_days", "p85_days", "previous_p50", "previous_p85", "previous_at")


def moved(current: Forecast, previous: Forecast | None) -> dict[str, Any]:
    """How many days P50 and P85 moved since ``previous``, and what they were; all None without."""
    if previous is None:
        return dict.fromkeys(MOVED_KEYS)
    return {
        "p50_days": _days_moved(current.p50, previous.p50),
        "p85_days": _days_moved(current.p85, previous.p85),
        "previous_p50": _iso(previous.p50),
        "previous_p85": _iso(previous.p85),
        "previous_at": _iso(previous.created_at),
    }


def _entry(db: Session, kind: str, subject: str, kind_note: str) -> dict[str, Any]:
    """The latest forecast of ``subject`` with its label, kind note, move and trail."""
    rows = _history(db, kind, subject, TRAIL)
    latest_row = serialize(rows[0])
    for key in ("inputs_hash", "seed"):
        del latest_row[key]
    return {
        **latest_row,
        "label": epic_label(subject),
        "kind_note": kind_note,
        "moved": moved(rows[0], rows[1] if len(rows) > 1 else None),
        "trail": [
            {"at": _iso(row.created_at), "p50": _iso(row.p50), "p85": _iso(row.p85)}
            for row in reversed(rows)
        ],
    }


def _milestone_order(name: str) -> tuple[int, str]:
    """M5 before M6 before M10 (by the number after M); untitled milestones last, by name."""
    match = MILESTONE_TITLE.match(name.strip())
    return (int(match.group(1)[1:]) if match else sys.maxsize, name)


def dashboard(db: Session) -> dict[str, Any]:
    """Everything the forecast page shows, read from saved forecasts; it never simulates.

    ``epics`` holds each open milestone's latest forecast in milestone order, then each
    simulated epic's by name; a closed milestone's forecasts stay saved but are not listed.
    ``sprint`` is the newest simulated sprint's. ``proposals`` stays empty until the planner.
    """
    newest = db.scalars(
        select(Forecast).order_by(Forecast.created_at.desc(), Forecast.id.desc()).limit(1)
    ).first()
    sprint = db.scalars(
        select(Forecast)
        .where(Forecast.kind == "sprint")
        .order_by(Forecast.created_at.desc(), Forecast.id.desc())
        .limit(1)
    ).first()
    open_milestones = {epic.name for epic in real_epics(db)}
    real, simulated = [], []
    subjects = db.scalars(select(Forecast.subject).where(Forecast.kind == "epic").distinct())
    for subject in subjects:
        if latest(db, "epic", subject).source == SOURCE:
            simulated.append(subject)
        elif subject in open_milestones:
            real.append(subject)
    return {
        "epics": [
            _entry(db, "epic", subject, REAL_NOTE) for subject in sorted(real, key=_milestone_order)
        ]
        + [_entry(db, "epic", subject, SIMULATED_NOTE) for subject in sorted(simulated)],
        "sprint": _entry(db, "sprint", sprint.subject, SIMULATED_NOTE) if sprint else None,
        "proposals": {},
        "saved": db.scalar(select(func.count(Forecast.id))),
        "as_of": _iso(newest.as_of) if newest else None,
    }


def reset(db: Session) -> None:
    """Forget the simulated forecasts and the forecaster's audit rows about them.

    Real forecasts (``github`` or ``mixed``), their audit rows and every other agent's rows are
    kept: the real work they describe is unchanged. Flushes, never commits.
    """
    db.execute(
        delete(AgentDecision).where(
            AgentDecision.agent == AGENT, AgentDecision.subject_source == SOURCE
        )
    )
    db.execute(delete(Forecast).where(Forecast.source == SOURCE))
    db.flush()


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.forecaster", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("once", help="save a forecast of whatever changed, and print the summary")
    commands.add_parser("now", help="save a fresh forecast of everything, and print the summary")
    args = parser.parse_args(argv)

    runner = ForecastRunner(get_settings().orchestrator_mode, engine=engine)
    print(json.dumps(runner.poll_once(force=args.command == "now")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
