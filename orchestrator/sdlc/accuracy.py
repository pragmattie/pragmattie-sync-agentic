"""How often the orchestrator's predictions matched what happened, and whether that is improving.

Five trends, each marked simulated or real:

- **Delivery forecasts (simulated):** each past synthetic sprint is forecast again as of its
  third working day, from only what was known then, and graded against when its work finished.
- **Risk score (simulated):** each merged synthetic PR is scored and tiered; an incident PR is
  caught when its tier is T2 or above.
- **Triage (real):** the agent's labels on real issues, kept or corrected by a person since.
- **Test selector (real):** settled ``ci_result`` rows, and how many missed a failing job.
- **Milestone forecasts (real):** each finished real milestone's first saved forecast against
  when its last issue closed.

No Claude call and no GitHub call. ``python -m sdlc.accuracy`` prints the report as JSON.
"""

import json
import sys
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import scoring
from sdlc.agents import suite_select, triage
from sdlc.audit import TRIAL
from sdlc.clock import utcnow
from sdlc.db import get_engine
from sdlc.epics import epic_label, milestone_order
from sdlc.forecast import (
    HISTORY_DAYS,
    RUNS,
    daily_throughput,
    percentile_date,
    seed_for,
    simulate,
    working_days,
)
from sdlc.governance import assign_tier, facts_of
from sdlc.suite_selector import RESULT
from sdlc.tables import AgentDecision, Epic, Forecast, Issue, PullRequest, Sprint
from sdlc.tiers import TIER_IDS, Policy, load_policy

SIMULATED = "synthetic"
AS_OF_WORKDAY = 2  # 0-based: each sprint is forecast as of its third working day
MIN_HISTORY_DAYS = 10
MIN_POINTS = 5
TARGETS = {"p85_hit_rate": 0.85, "p50_hit_rate": 0.5}
DIMENSIONS = ("module", "type", "priority", "points")
FLAGGED_FROM = "T2"
TOO_EARLY = "too early in the history to have forecast it"
STILL_OPEN = "still open"
FORECAST_TOO_LATE = "first forecast came after its work had started closing"

# The simulated trends are slow, so they are kept per (today, newest synthetic PR, newest
# synthetic issue); a new key clears the cache.
_cache: dict[tuple, dict] = {}


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def _start_of(day: date) -> datetime:
    return datetime.combine(day, time())


def workday_error(actual: date, forecast: date) -> int:
    """Working days ``actual`` was after ``forecast``; negative when it was before."""
    if actual >= forecast:
        return len(working_days(forecast + timedelta(days=1), actual))
    return -len(working_days(actual + timedelta(days=1), forecast))


def _met(forecast: date | None, actual: date | None) -> bool:
    """An unfinished piece of work misses; otherwise no date at all, or on or before it, hits."""
    if actual is None:
        return False
    return forecast is None or actual <= forecast


def _known_throughput(db: Session, as_of: date) -> list[int]:
    """``daily_throughput`` before ``as_of``, from the history's first closure on."""
    samples = daily_throughput(db, today=as_of, source=SIMULATED)
    began = db.scalar(select(func.min(Issue.closed_at)).where(Issue.source == SIMULATED))
    if began is None:
        return []
    window = working_days(as_of - timedelta(days=HISTORY_DAYS), as_of - timedelta(days=1))
    return [count for day, count in zip(window, samples, strict=True) if day >= began.date()]


def backtest_sprint(db: Session, sprint: Sprint, *, runs: int = RUNS) -> dict | str:
    """``sprint`` forecast as of its third working day and graded; a reason string if skipped."""
    days = working_days(sprint.start_date, sprint.end_date)
    if len(days) <= AS_OF_WORKDAY:
        return "too short to forecast"
    as_of = days[AS_OF_WORKDAY]
    start = _start_of(as_of)
    items = list(
        db.scalars(
            select(Issue).where(
                Issue.sprint_id == sprint.id,
                Issue.created_at < start,
                (Issue.closed_at.is_(None)) | (Issue.closed_at >= start),
            )
        )
    )
    samples = _known_throughput(db, as_of)
    if len(samples) < MIN_HISTORY_DAYS:
        return TOO_EARLY
    results = simulate(len(items), samples, start=as_of, runs=runs, seed=seed_for(sprint, as_of))
    p50, p85 = percentile_date(results, 0.5), percentile_date(results, 0.85)
    if items and p85 is None:
        return "no finish within the forecast horizon"
    if not items:
        actual = as_of
    elif any(item.closed_at is None for item in items):
        actual = None
    else:
        actual = max(item.closed_at for item in items).date()
    return {
        "sprint": sprint.name,
        "as_of": as_of,
        "end_date": sprint.end_date,
        "items": len(items),
        "p50": p50,
        "p85": p85,
        "actual": actual,
        "p50_met": _met(p50, actual),
        "p85_met": _met(p85, actual),
        "p50_error_days": workday_error(actual, p50) if actual and p50 else None,
    }


def forecast_accuracy(db: Session, today: date, runs: int = RUNS) -> dict:
    """Every synthetic sprint that ended before ``today``, backtested, with running hit rates."""
    sprints = db.scalars(
        select(Sprint)
        .where(Sprint.source == SIMULATED, Sprint.end_date < today)
        .order_by(Sprint.start_date, Sprint.id)
    )
    rows = []
    hits = {"p50": 0, "p85": 0}
    for sprint in sprints:
        row = backtest_sprint(db, sprint, runs=runs)
        if isinstance(row, str):
            continue
        hits["p50"] += row["p50_met"]
        hits["p85"] += row["p85_met"]
        row["p50_hit_rate_so_far"] = _rate(hits["p50"], len(rows) + 1)
        row["p85_hit_rate_so_far"] = _rate(hits["p85"], len(rows) + 1)
        rows.append(row)
    return {
        "simulated": True,
        "sprints": rows,
        "graded": len(rows),
        "p50_hit_rate": _rate(hits["p50"], len(rows)),
        "p85_hit_rate": _rate(hits["p85"], len(rows)),
        "targets": TARGETS,
        "as_of_workday": AS_OF_WORKDAY + 1,
    }


def risk_accuracy(db: Session, policy: Policy) -> dict:
    """Merged synthetic PRs by the sprint they merged in: incident PRs and how many were caught."""
    flagged = set(TIER_IDS[TIER_IDS.index(FLAGGED_FROM) :])
    sprints = list(
        db.scalars(
            select(Sprint).where(Sprint.source == SIMULATED).order_by(Sprint.start_date, Sprint.id)
        )
    )
    counts = {sprint.id: {"prs": 0, "incident_prs": 0, "caught": 0, "t0": 0} for sprint in sprints}
    prs = db.scalars(
        select(PullRequest)
        .where(PullRequest.source == SIMULATED, PullRequest.merged_at.is_not(None))
        .order_by(PullRequest.id)
    )
    for pr in prs:
        merged = pr.merged_at.date()
        sprint = next((s for s in sprints if s.start_date <= merged <= s.end_date), None)
        if sprint is None:
            continue
        tier = assign_tier(policy, scoring.score_pull_request(db, pr).total, facts_of(pr)).tier
        row = counts[sprint.id]
        row["prs"] += 1
        if pr.caused_incident:
            row["incident_prs"] += 1
            row["caught"] += tier in flagged
            row["t0"] += tier == "T0"
    rows = []
    incidents = caught = t0 = 0
    for sprint in sprints:
        row = counts[sprint.id]
        incidents += row["incident_prs"]
        caught += row["caught"]
        t0 += row["t0"]
        rows.append(
            {
                "sprint": sprint.name,
                "prs": row["prs"],
                "incident_prs": row["incident_prs"],
                "caught": row["caught"],
                "t0_incidents": row["t0"],
                "recall_so_far": _rate(caught, incidents),
            }
        )
    real_merged, real_incidents = db.execute(
        select(
            func.count(PullRequest.id),
            func.coalesce(func.sum(case((PullRequest.caused_incident.is_(True), 1), else_=0)), 0),
        ).where(PullRequest.source == "github", PullRequest.merged_at.is_not(None))
    ).one()
    return {
        "simulated": True,
        "flagged_from": FLAGGED_FROM,
        "sprints": rows,
        "incident_prs": incidents,
        "caught": caught,
        "recall": _rate(caught, incidents),
        "t0_incidents": t0,
        # MySQL sums to a Decimal; the report's counts are ints.
        "real": {"merged_prs": int(real_merged), "incident_prs": int(real_incidents)},
    }


def _week(moment: datetime) -> date:
    return moment.date() - timedelta(days=moment.weekday())


def _field(dimension: str) -> str:
    return "estimate_points" if dimension == "points" else dimension


def _changed(issue: Issue | None, said: dict) -> list[str]:
    """The dimensions whose current value on ``issue`` is set and differs from what was said."""
    if issue is None:
        return []
    return [
        dim
        for dim in DIMENSIONS
        if getattr(issue, _field(dim)) is not None
        and getattr(issue, _field(dim)) != said.get(_field(dim))
    ]


def triage_accuracy(db: Session) -> dict:
    """The agent's newest labels on each real issue, by week: kept, or corrected by a person."""
    query = select(AgentDecision).where(
        AgentDecision.agent == triage.AGENT,
        AgentDecision.subject_type == "issue",
        AgentDecision.subject_source == "github",
        AgentDecision.status == "ok",
        AgentDecision.trigger != TRIAL,
    )
    newest: dict[int, AgentDecision] = {}
    for row in db.scalars(query.order_by(AgentDecision.created_at, AgentDecision.id)):
        newest[row.subject_id] = row
    issues = {
        issue.number: issue
        for issue in db.scalars(
            select(Issue).where(Issue.source == "github", Issue.number.in_(list(newest)))
        )
    }
    weeks: dict[date, dict] = defaultdict(
        lambda: {"labelled": 0, "corrected": 0, **{f"{dim}_changed": 0 for dim in DIMENSIONS}}
    )
    corrected = 0
    for number, row in newest.items():
        week = weeks[_week(row.created_at)]
        week["labelled"] += 1
        changed = _changed(issues.get(number), row.output or {})
        for dim in changed:
            week[f"{dim}_changed"] += 1
        if changed:
            week["corrected"] += 1
            corrected += 1
    total = len(newest)
    return {
        "simulated": False,
        "weeks": [{"week": week, **weeks[week]} for week in sorted(weeks)],
        "total": total,
        "enough": total >= MIN_POINTS,
        "corrected": corrected,
        "kept_rate": _rate(total - corrected, total),
    }


def selector_accuracy(db: Session) -> dict:
    """Settled test selector results on real commits, by week, and how many missed a failure."""
    rows = db.scalars(
        select(AgentDecision)
        .where(
            AgentDecision.agent == suite_select.AGENT,
            AgentDecision.trigger == RESULT,
            AgentDecision.subject_source == "github",
        )
        .order_by(AgentDecision.created_at, AgentDecision.id)
    )
    weeks: dict[date, dict] = defaultdict(lambda: {"settled": 0, "missed": 0})
    total = missed = 0
    for row in rows:
        week = weeks[_week(row.created_at)]
        week["settled"] += 1
        total += 1
        if row.status == "missed":
            week["missed"] += 1
            missed += 1
    return {
        "simulated": False,
        "weeks": [{"week": week, **weeks[week]} for week in sorted(weeks)],
        "total": total,
        "enough": total >= MIN_POINTS,
        "missed": missed,
        "miss_rate": _rate(missed, total),
    }


def milestone_accuracy(db: Session) -> dict:
    """Each real milestone with saved forecasts; finished ones forecast in time are graded."""
    epics = sorted(
        db.scalars(select(Epic).where(Epic.source == "github")),
        key=lambda epic: milestone_order(epic.name),
    )
    rows = []
    hits = {"p50": 0, "p85": 0}
    for epic in epics:
        first = db.scalars(
            select(Forecast)
            .where(Forecast.kind == "epic", Forecast.subject == epic.name)
            .order_by(Forecast.created_at, Forecast.id)
            .limit(1)
        ).first()
        if first is None:
            continue
        closed = [
            issue.closed_at
            for issue in db.scalars(
                select(Issue).where(Issue.epic == epic.name, Issue.source == "github")
            )
        ]
        finished = bool(closed) and all(closed)
        actual = max(closed).date() if finished else None
        reason = None
        if not finished:
            reason = STILL_OPEN
        elif first.created_at >= min(closed):
            reason = FORECAST_TOO_LATE
        graded = reason is None
        row = {
            "milestone": epic.name,
            "label": epic_label(epic.name),
            "due_on": epic.due_on,
            "first_forecast_at": first.created_at,
            "p50": first.p50,
            "p85": first.p85,
            "actual": actual,
            "p50_met": None,
            "p85_met": None,
            "p50_error_days": None,
            "target_met": None,
            "graded": graded,
            "reason": reason,
        }
        if graded:
            row["p50_met"] = _met(first.p50, actual)
            row["p85_met"] = _met(first.p85, actual)
            row["p50_error_days"] = workday_error(actual, first.p50) if first.p50 else None
            row["target_met"] = actual <= epic.due_on if epic.due_on else None
            hits["p50"] += row["p50_met"]
            hits["p85"] += row["p85_met"]
        rows.append(row)
    total = sum(1 for row in rows if row["graded"])
    return {
        "simulated": False,
        "milestones": rows,
        "total": total,
        "enough": total >= MIN_POINTS,
        "p50_hit_rate": _rate(hits["p50"], total),
        "p85_hit_rate": _rate(hits["p85"], total),
    }


def _cache_key(db: Session, today: date) -> tuple:
    newest_pr = db.scalar(select(func.max(PullRequest.id)).where(PullRequest.source == SIMULATED))
    newest_issue = db.scalar(select(func.max(Issue.id)).where(Issue.source == SIMULATED))
    return today, newest_pr, newest_issue


def report(db: Session, today: date | None = None) -> dict:
    """Every trend; the simulated ones come from the cache when the history hasn't changed."""
    today = today or utcnow().date()
    key = _cache_key(db, today)
    simulated = _cache.get(key)
    if simulated is None:
        simulated = {
            "forecast": forecast_accuracy(db, today, runs=RUNS),
            "risk": risk_accuracy(db, load_policy()),
        }
        _cache.clear()
        _cache[key] = simulated
    return {
        **simulated,
        "triage": triage_accuracy(db),
        "test_selector": selector_accuracy(db),
        "milestones": milestone_accuracy(db),
        "min_points": MIN_POINTS,
        "generated_at": utcnow(),
    }


def _json_default(value):
    if isinstance(value, date | datetime):
        return value.isoformat()
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    with Session(engine or get_engine()) as db:
        print(json.dumps(report(db), indent=2, default=_json_default))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
