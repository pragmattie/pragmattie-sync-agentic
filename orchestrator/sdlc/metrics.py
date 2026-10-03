"""How the team delivers: DORA measures, velocity, PR cycle time, CI health and quality.

Plain functions over the engineering history. Each takes an explicit ``now`` or ``today`` and
computes in Python over the rows, so the figures are the same on MySQL and SQLite. A measure with
no data is ``None``. Rows from every source are included.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc.tables import SOURCES, CIRun, Deployment, Incident, Issue, PullRequest, Sprint


def _percentile(values: list[float], pct: float) -> float | None:
    """Linear interpolation between the closest ranks, at index (n - 1) * pct."""
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * pct
    lower = int(index)
    upper = min(lower + 1, len(ordered) - 1)
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (index - lower)


def _hours(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 3600


def _round(value: float | None, places: int = 1) -> float | None:
    return None if value is None else round(value, places)


def _median(values: list[float]) -> float | None:
    return _round(median(values)) if values else None


def _percent(part: int, whole: int) -> float | None:
    return _round(100 * part / whole) if whole else None


def _monday(day: date) -> date:
    return day - timedelta(days=day.weekday())


def _midnight(day: date) -> datetime:
    return datetime.combine(day, time())


def _merged_prs(db: Session) -> list[PullRequest]:
    return list(db.scalars(select(PullRequest).where(PullRequest.merged_at.is_not(None))))


def _cycle_hours(prs: list[PullRequest]) -> dict:
    hours = [_hours(pr.created_at, pr.merged_at) for pr in prs]
    return {
        "median_hours": _median(hours),
        "p85_hours": _round(_percentile(hours, 0.85)),
        "merged": len(hours),
    }


def _dora_window(db: Session, start: datetime, end: datetime, days: int) -> dict:
    deployments = db.scalars(
        select(Deployment).where(Deployment.deployed_at >= start, Deployment.deployed_at < end)
    ).all()
    incidents = db.scalars(
        select(Incident).where(Incident.opened_at >= start, Incident.opened_at < end)
    ).all()
    merged = db.scalars(
        select(PullRequest).where(PullRequest.merged_at >= start, PullRequest.merged_at < end)
    ).all()

    incident_deploys = {i.deployment_id for i in incidents if i.deployment_id is not None}
    failed = sum(1 for d in deployments if d.status == "rolled_back" or d.id in incident_deploys)
    restore = [_hours(i.opened_at, i.resolved_at) for i in incidents if i.resolved_at is not None]
    return {
        "deploys_per_week": _round(len(deployments) / (days / 7)) if deployments else None,
        "lead_time_hours": _median([_hours(pr.created_at, pr.merged_at) for pr in merged]),
        "change_failure_rate": _percent(failed, len(deployments)),
        "time_to_restore_hours": _median(restore),
        "deployments": len(deployments),
        "incidents": len(incidents),
    }


def dora_summary(db: Session, now: datetime, days: int = 30) -> dict:
    """The four DORA measures for [now - days, now) and for the ``days`` before that."""
    span = timedelta(days=days)
    return {
        "days": days,
        "current": _dora_window(db, now - span, now, days),
        "previous": _dora_window(db, now - 2 * span, now - span, days),
    }


def _sprints(db: Session) -> list[Sprint]:
    return list(db.scalars(select(Sprint).order_by(Sprint.start_date, Sprint.id)))


def sprint_velocity(db: Session, today: date) -> list[dict]:
    """Points committed and completed per sprint, oldest first."""
    issues_by_sprint = defaultdict(list)
    for issue in db.scalars(select(Issue).where(Issue.sprint_id.is_not(None))):
        issues_by_sprint[issue.sprint_id].append(issue)

    rows = []
    for sprint in _sprints(db):
        issues = issues_by_sprint[sprint.id]
        cutoff = _midnight(sprint.end_date + timedelta(days=1))
        rows.append(
            {
                "sprint": sprint.name,
                "start": sprint.start_date,
                "end": sprint.end_date,
                "goal": sprint.goal,
                "in_progress": sprint.start_date <= today <= sprint.end_date,
                "committed": sum(i.estimate_points or 0 for i in issues),
                "completed": sum(
                    i.estimate_points or 0
                    for i in issues
                    if i.closed_at is not None and i.closed_at < cutoff
                ),
            }
        )
    return rows


def pr_cycle_time_by_sprint(db: Session, now: datetime) -> list[dict]:
    """Hours from opened to merged for PRs merged during each sprint, oldest first."""
    merged = _merged_prs(db)
    today = now.date()
    rows = []
    for sprint in _sprints(db):
        start = _midnight(sprint.start_date)
        end = _midnight(sprint.end_date + timedelta(days=1))
        prs = [pr for pr in merged if start <= pr.merged_at < end]
        rows.append(
            {
                "sprint": sprint.name,
                "start": sprint.start_date,
                "in_progress": sprint.start_date <= today <= sprint.end_date,
                **_cycle_hours(prs),
            }
        )
    return rows


def _weeks(weeks: int, now: datetime) -> list[date]:
    """Mondays of the last ``weeks`` calendar weeks, the week of ``now`` last."""
    current = _monday(now.date())
    return [current - timedelta(weeks=n) for n in range(weeks - 1, -1, -1)]


def pr_cycle_time(db: Session, weeks: int, now: datetime) -> list[dict]:
    """Hours from opened to merged per calendar week merged, the last ``weeks`` weeks."""
    mondays = _weeks(weeks, now)
    by_week = defaultdict(list)
    for pr in _merged_prs(db):
        if pr.merged_at < now:
            by_week[_monday(pr.merged_at.date())].append(pr)
    return [{"week": monday, **_cycle_hours(by_week[monday])} for monday in mondays]


def ci_health(db: Session, weeks: int, now: datetime) -> dict:
    """Pass and flaky rates of CI runs started in the last ``weeks`` calendar weeks."""
    mondays = _weeks(weeks, now)
    runs = db.scalars(
        select(CIRun).where(CIRun.started_at >= _midnight(mondays[0]), CIRun.started_at < now)
    ).all()

    by_suite = defaultdict(list)
    by_week = defaultdict(list)
    for run in runs:
        by_suite[run.suite].append(run)
        by_week[_monday(run.started_at.date())].append(run)

    def passed(group: list[CIRun]) -> int:
        return sum(1 for run in group if run.conclusion == "success")

    return {
        "by_suite": [
            {
                "suite": suite,
                "runs": len(group),
                "pass_rate": _percent(passed(group), len(group)),
                "flaky_rate": _percent(sum(1 for run in group if run.flaky), len(group)),
            }
            for suite, group in sorted(by_suite.items())
        ],
        "by_week": [
            {
                "week": monday,
                "runs": len(by_week[monday]),
                "pass_rate": _percent(passed(by_week[monday]), len(by_week[monday])),
            }
            for monday in mondays
        ],
    }


def quality_by_module(db: Session) -> list[dict]:
    """Incidents and effort per point for each module with merged PRs, worst first."""
    prs_by_module = defaultdict(list)
    for pr in _merged_prs(db):
        if pr.module:
            prs_by_module[pr.module].append(pr)

    incidents = defaultdict(int)
    for module in db.scalars(select(Incident.module)):
        incidents[module] += 1

    days = defaultdict(float)
    points = defaultdict(int)
    for issue in db.scalars(
        select(Issue).where(
            Issue.closed_at.is_not(None),
            Issue.estimate_points.is_not(None),
            Issue.actual_days.is_not(None),
        )
    ):
        days[issue.module] += issue.actual_days
        points[issue.module] += issue.estimate_points

    rows = [
        {
            "module": module,
            "merged_prs": len(prs),
            "incidents": incidents[module],
            "incident_rate": _percent(sum(1 for pr in prs if pr.caused_incident), len(prs)),
            "days_per_point": round(days[module] / points[module], 2) if points[module] else None,
        }
        for module, prs in sorted(prs_by_module.items())
    ]
    rows.sort(key=lambda row: row["incident_rate"], reverse=True)
    return rows


def sources(db: Session) -> dict[str, dict[str, int]]:
    """Row counts per source, so readers can see how much of the data is simulated."""
    tables = {
        "issues": Issue,
        "pull_requests": PullRequest,
        "ci_runs": CIRun,
        "deployments": Deployment,
        "incidents": Incident,
    }
    result = {}
    for name, table in tables.items():
        counts = dict.fromkeys(SOURCES, 0)
        for source, count in db.execute(
            select(table.source, func.count(table.id)).group_by(table.source)
        ):
            counts[source] = count
        result[name] = counts
    return result
