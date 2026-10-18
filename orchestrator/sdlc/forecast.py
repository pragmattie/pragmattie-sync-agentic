"""Monte Carlo delivery forecast: when will the open work be done?

The forecast resamples how many issues were actually closed on each recent working day and plays
the remaining work forward ``RUNS`` times. The spread of finish dates gives P50 (a coin flip) and
P85 (the date to promise). The forecast is seeded from the sprint and the day, so the same day
always gives the same numbers and a demo repeats. Each run has its own random sequence, drawn from
that seed and the run's number, so a run sees the same days whatever is remaining: fewer items can
never finish later.

Sprints are simulated only: nothing assigns real issues a sprint, so the sprint forecast covers the
simulated history (calibration data). Real work is forecast by milestone: each open GitHub milestone
is an epic, forecast at the agents' pace across all real work and in sequence: the agents finish one
milestone before the next starts, so a milestone's forecast plays forward the open items in the
earlier open milestones and then its own, with one seed per day shared by every milestone. Each
milestone boundary still ahead (a person's gate, then the next milestone's specs and their
approval) adds the gap measured between past milestones (``gate_gap``). The simulated history's
epics are forecast at their own pace, each alone.

Run ``python -m sdlc.forecast`` to print the current simulated sprint's forecast.
"""

import argparse
import math
import random
import statistics
import sys
import zlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.clock import utcnow
from sdlc.db import get_engine
from sdlc.epics import epic_label, milestone_order
from sdlc.tables import Epic, Issue, PullRequest, Sprint

HISTORY_DAYS = 84  # six two-week sprints
RUNS = 10_000
HORIZON = 260  # working days, about a year
OVERRUN_FACTOR = 1.5
OVERRUN_SLACK_DAYS = 2
DEFAULT_DAYS_PER_POINT = 1.0  # v1's rate when there is no history at all
RUN_SEED_STEP = 1_000_003  # run i of seed s draws from random.Random(s * RUN_SEED_STEP + i)
DEFAULT_GATE_GAP = 1  # working days between milestones while too few have been measured
MIN_GATE_SAMPLES = 3


def is_workday(day: date) -> bool:
    return day.weekday() < 5


def working_days(start: date, end: date) -> list[date]:
    """The working days from ``start`` to ``end``, both included."""
    return [
        start + timedelta(days=offset)
        for offset in range((end - start).days + 1)
        if is_workday(start + timedelta(days=offset))
    ]


def next_workday(day: date) -> date:
    """The first working day after ``day``."""
    day += timedelta(days=1)
    while not is_workday(day):
        day += timedelta(days=1)
    return day


def previous_workday(day: date) -> date:
    """The last working day before ``day``."""
    day -= timedelta(days=1)
    while not is_workday(day):
        day -= timedelta(days=1)
    return day


def _first_workday(day: date) -> date:
    """``day`` itself if it is a working day, otherwise the next one."""
    return day if is_workday(day) else next_workday(day)


def daily_throughput(
    db: Session,
    *,
    today: date,
    source: str | None = None,
    epic: str | None = None,
    days: int = HISTORY_DAYS,
) -> list[int]:
    """Issues closed on each working day of ``[today - days, today - 1]``; today is unfinished."""
    first = today - timedelta(days=days)
    query = select(Issue.closed_at).where(
        Issue.closed_at >= datetime.combine(first, time()),
        Issue.closed_at < datetime.combine(today, time()),
    )
    if source is not None:
        query = query.where(Issue.source == source)
    if epic is not None:
        query = query.where(Issue.epic == epic)
    closed = Counter(closed_at.date() for closed_at in db.scalars(query))
    return [closed[day] for day in working_days(first, today - timedelta(days=1))]


def real_throughput(db: Session, *, today: date, days: int = HISTORY_DAYS) -> list[int]:
    """Real issues closed on each working day since the first real closure, up to yesterday.

    The window starts at the later of ``today - days`` and the earliest real closure, so the days
    before the real work existed aren't sampled. A weekend closure counts on the next working day,
    or, when that is today or later (a forecast made on a Sunday or Monday), on the last working
    day before today, so no closure is left out; the window then starts at the earliest day a
    closure is counted on.
    """
    earliest = db.scalar(select(func.min(Issue.closed_at)).where(Issue.source == "github"))
    if earliest is None:
        return []
    start = max(today - timedelta(days=days), earliest.date())
    query = select(Issue.closed_at).where(
        Issue.source == "github",
        Issue.closed_at >= datetime.combine(start, time()),
        Issue.closed_at < datetime.combine(today, time()),
    )
    last = previous_workday(today)
    counted = [min(_first_workday(closed_at.date()), last) for closed_at in db.scalars(query)]
    # A weekend closure moved back to Friday may fall before ``start``; the window starts there.
    first = min([start, *counted])
    closed = Counter(counted)
    return [closed[day] for day in working_days(first, today - timedelta(days=1))]


def simulate(
    remaining: int, samples: list[int], *, start: date, runs: int, seed: int
) -> list[date | None]:
    """Each run's finish date, or ``None`` when it is not done within ``HORIZON`` working days.

    Run ``i`` draws from its own sequence, seeded from ``(seed, i)``, so it sees the same days
    whatever is remaining.
    """
    first = _first_workday(start)
    if remaining <= 0:
        return [first] * runs
    if not any(samples):
        return [None] * runs
    results: list[date | None] = []
    for run in range(runs):
        rng = random.Random(seed * RUN_SEED_STEP + run)
        left = remaining
        day = first
        finished = None
        for _ in range(HORIZON):
            left -= rng.choice(samples)
            if left <= 0:
                finished = day
                break
            day = next_workday(day)
        results.append(finished)
    return results


def on_time_share(results: list[date | None], end_date: date, remaining: int) -> float:
    """The share of runs finished by ``end_date``; 1.0 with nothing remaining (already done)."""
    if remaining <= 0:
        return 1.0
    if not results:
        return 0.0
    return sum(1 for day in results if day is not None and day <= end_date) / len(results)


def percentile_date(results: list[date | None], pct: float) -> date | None:
    """The ``pct`` percentile of ``results``, with unfinished runs (``None``) sorted last."""
    if not results:
        return None
    ordered = sorted(results, key=lambda day: (day is None, day or date.min))
    return ordered[max(0, math.ceil(pct * len(ordered)) - 1)]


def seed_for(sprint: Sprint, today: date) -> int:
    return zlib.crc32(f"{sprint.source}:{sprint.id}:{today.isoformat()}".encode())


def epic_seed(epic: str, today: date) -> int:
    return zlib.crc32(f"epic:{epic}:{today.isoformat()}".encode())


def days_per_point(db: Session, source: str) -> dict[str | None, float]:
    """Working days per point over closed, estimated issues, by module; key ``None`` is overall."""
    rows = db.execute(
        select(Issue.module, func.sum(Issue.actual_days), func.sum(Issue.estimate_points))
        .where(
            Issue.source == source,
            Issue.state == "closed",
            Issue.actual_days.is_not(None),
            Issue.estimate_points > 0,
        )
        .group_by(Issue.module)
    ).all()
    rates: dict[str | None, float] = {}
    total_days = total_points = 0.0
    for module, days, points in rows:
        # MySQL's SUM of an integer column is a Decimal; SQLite's is a number.
        days, points = float(days), float(points)
        total_days += days
        total_points += points
        if module is not None:
            rates[module] = days / points
    if total_points:
        rates[None] = total_days / total_points
    return rates


@dataclass
class ItemRisk:
    number: int | None
    title: str
    module: str | None
    points: int
    expected_days: float
    remaining_days: float
    started: date | None
    reason: str


def started_on(db: Session, issue_ids: list[int]) -> dict[int, date]:
    """The day each issue's first linked pull request was opened; unstarted issues are absent."""
    if not issue_ids:
        return {}
    rows = db.execute(
        select(PullRequest.issue_id, func.min(PullRequest.created_at))
        .where(PullRequest.issue_id.in_(issue_ids))
        .group_by(PullRequest.issue_id)
    ).all()
    return {issue_id: opened.date() for issue_id, opened in rows}


def _at_risk(
    db: Session, items: list[Issue], *, today: date, days_left: int, source: str
) -> list[ItemRisk]:
    """Open items that are well over their expected time or no longer fit in ``days_left``."""
    rates = days_per_point(db, source)
    opened = started_on(db, [item.id for item in items])
    flagged: list[ItemRisk] = []
    for item in items:
        points = item.estimate_points or 0
        module = item.module or "unassigned"
        rate = rates.get(item.module, rates.get(None, DEFAULT_DAYS_PER_POINT))
        expected = points * rate
        started = opened.get(item.id)
        elapsed = len(working_days(started, today - timedelta(days=1))) if started else 0
        remaining = max(0.0, expected - elapsed)
        history = f"{module} work has taken {rate:.1f} working days per point"
        overrunning = started is not None and elapsed >= max(
            expected * OVERRUN_FACTOR, expected + OVERRUN_SLACK_DAYS
        )
        if overrunning:
            reason = (
                f"In progress {elapsed} working days against about {expected:.1f} expected "
                f"({points} pt; {history}): well over, still open."
            )
        elif remaining > days_left:
            status = f"started {started:%b %d}" if started else "not started"
            reason = (
                f"{points} pt, {status}, about {remaining:.1f} working days of work left but "
                f"{days_left} left in the sprint ({history})."
            )
        else:
            continue
        flagged.append(
            ItemRisk(
                number=item.number,
                title=item.title,
                module=item.module,
                points=points,
                expected_days=expected,
                remaining_days=remaining,
                started=started,
                reason=reason,
            )
        )
    return sorted(flagged, key=lambda risk: risk.remaining_days - days_left, reverse=True)


def current_sprint(db: Session, today: date, source: str) -> Sprint | None:
    """The ``source`` sprint in progress on ``today``."""
    return db.scalars(
        select(Sprint)
        .where(Sprint.source == source, Sprint.start_date <= today, Sprint.end_date >= today)
        .order_by(Sprint.start_date.desc())
        .limit(1)
    ).first()


@dataclass
class SprintForecast:
    sprint: str
    source: str
    start_date: date
    end_date: date
    as_of: date
    remaining_items: int
    remaining_points: int
    working_days_left: int
    p50: date | None
    p85: date | None
    on_time_probability: float
    throughput_mean: float
    history_days: int
    runs: int
    seed: int
    at_risk: list[ItemRisk] = field(default_factory=list)


def sprint_forecast(
    db: Session,
    today: date,
    *,
    sprint: Sprint | None = None,
    source: str = "synthetic",
    runs: int = RUNS,
) -> SprintForecast | None:
    """The forecast for ``sprint`` (by default the one in progress); ``None`` if there is none."""
    sprint = sprint or current_sprint(db, today, source)
    if sprint is None:
        return None
    items = list(
        db.scalars(
            select(Issue)
            .where(Issue.sprint_id == sprint.id, Issue.state == "open")
            .order_by(Issue.id)
        )
    )
    samples = daily_throughput(db, today=today, source=sprint.source)
    seed = seed_for(sprint, today)
    results = simulate(len(items), samples, start=today, runs=runs, seed=seed)
    days_left = len(working_days(today, sprint.end_date))
    return SprintForecast(
        sprint=sprint.name,
        source=sprint.source,
        start_date=sprint.start_date,
        end_date=sprint.end_date,
        as_of=today,
        remaining_items=len(items),
        remaining_points=sum(item.estimate_points or 0 for item in items),
        working_days_left=days_left,
        p50=percentile_date(results, 0.5),
        p85=percentile_date(results, 0.85),
        on_time_probability=on_time_share(results, sprint.end_date, len(items)),
        throughput_mean=round(sum(samples) / len(samples), 2) if samples else 0.0,
        history_days=HISTORY_DAYS,
        runs=runs,
        seed=seed,
        at_risk=_at_risk(db, items, today=today, days_left=days_left, source=sprint.source),
    )


def _day(day: date | None) -> str:
    return f"{day:%a %b %d}" if day else f"not within {HORIZON} working days"


def describe(forecast: SprintForecast) -> str:
    """A plain-text summary of ``forecast``."""
    lines = [
        f"{forecast.sprint} ({forecast.source}), {forecast.start_date:%b %d} to "
        f"{forecast.end_date:%b %d}, as of {forecast.as_of:%a %b %d}",
        f"Remaining: {forecast.remaining_items} items ({forecast.remaining_points} pt), "
        f"{forecast.working_days_left} working days left in the sprint",
        f"P50: {_day(forecast.p50)}",
        f"P85: {_day(forecast.p85)}",
        f"Chance of finishing by the sprint end: {forecast.on_time_probability:.0%}",
        f"Based on {forecast.throughput_mean:.2f} items closed a working day over the last "
        f"{forecast.history_days} days ({forecast.runs:,} runs, seed {forecast.seed})",
    ]
    if forecast.at_risk:
        lines.append("At risk:")
        lines += [f"  #{risk.number} {risk.title}: {risk.reason}" for risk in forecast.at_risk]
    else:
        lines.append("At risk: none")
    return "\n".join(lines)


def real_epics(db: Session) -> list[Epic]:
    """The open GitHub milestones, by name."""
    return list(
        db.scalars(
            select(Epic).where(Epic.source == "github", Epic.state == "open").order_by(Epic.name)
        )
    )


def simulated_epics(db: Session) -> list[str]:
    """The distinct epic names on synthetic issues, sorted."""
    return list(
        db.scalars(
            select(Issue.epic)
            .where(Issue.source == "synthetic", Issue.epic.is_not(None))
            .distinct()
            .order_by(Issue.epic)
        )
    )


def real_epic(db: Session, name: str) -> Epic | None:
    """The open GitHub milestone titled ``name``."""
    return db.scalars(
        select(Epic)
        .where(Epic.source == "github", Epic.state == "open", Epic.name == name)
        .order_by(Epic.id)
        .limit(1)
    ).first()


def open_items_ahead(db: Session, epic: str) -> list[tuple[int, int | None, str]]:
    """The open real issues in the open real milestones before ``epic``: (id, points, milestone).

    Milestones are ordered by the number after M; a milestone with no number, and every
    simulated epic, has nothing ahead.
    """
    milestone = real_epic(db, epic)
    number = milestone_order(epic)[0]
    if milestone is None or number == sys.maxsize:
        return []
    earlier = [other.name for other in real_epics(db) if milestone_order(other.name)[0] < number]
    if not earlier:
        return []
    rows = db.execute(
        select(Issue.id, Issue.estimate_points, Issue.epic)
        .where(Issue.epic.in_(earlier), Issue.source == "github", Issue.state == "open")
        .order_by(Issue.id)
    ).all()
    return [(issue_id, points, name) for issue_id, points, name in rows]


def items_ahead(db: Session, epic: str) -> int:
    """How many open real issues are built before ``epic``'s (``open_items_ahead``)."""
    return len(open_items_ahead(db, epic))


def _milestone_prs(db: Session) -> dict[str, list[tuple[datetime, datetime | None]]]:
    """Each numbered real milestone's linked real pull requests: (opened, merged), any state."""
    rows = db.execute(
        select(Issue.epic, PullRequest.created_at, PullRequest.merged_at)
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.source == "github", Issue.source == "github", Issue.epic.is_not(None))
    ).all()
    prs: dict[str, list[tuple[datetime, datetime | None]]] = {}
    for epic, opened, merged in rows:
        if milestone_order(epic)[0] != sys.maxsize:
            prs.setdefault(epic, []).append((opened, merged))
    return prs


def gate_gaps(db: Session) -> list[int]:
    """The working days between consecutive real milestones, open and closed alike.

    Milestones with a merged real pull request are taken in milestone order; each gap counts the
    working days strictly after the earlier one's last merge, up to and including the day the
    later one's first pull request was opened. An overlap is 0.
    """
    spans = []
    by_milestone = sorted(_milestone_prs(db).items(), key=lambda item: milestone_order(item[0]))
    for _, prs in by_milestone:
        merges = [merged for _, merged in prs if merged is not None]
        if merges:
            spans.append((min(opened for opened, _ in prs).date(), max(merges).date()))
    return [
        len(working_days(last_merge + timedelta(days=1), first_opened))
        for (_, last_merge), (first_opened, _) in zip(spans, spans[1:], strict=False)
    ]


def gate_gap(db: Session) -> int:
    """The median gap (rounded half up), or ``DEFAULT_GATE_GAP`` with too few measured."""
    gaps = gate_gaps(db)
    if len(gaps) < MIN_GATE_SAMPLES:
        return DEFAULT_GATE_GAP
    return math.floor(statistics.median(gaps) + 0.5)


def gate_boundaries(db: Session, epic: str) -> int:
    """How many gates ``epic``'s work waits for: each open real milestone before it, or, with
    none, 1 while ``epic`` has no real pull request opened (its specs are still to come)."""
    number = milestone_order(epic)[0]
    earlier = sum(
        1
        for other in real_epics(db)
        if number != sys.maxsize and milestone_order(other.name)[0] < number
    )
    if earlier:
        return earlier
    started = db.scalars(
        select(PullRequest.id)
        .join(Issue, PullRequest.issue_id == Issue.id)
        .where(PullRequest.source == "github", Issue.source == "github", Issue.epic == epic)
        .limit(1)
    ).first()
    return 0 if started is not None else 1


def shift(results: list[date | None], days: int) -> list[date | None]:
    """Each finish ``days`` working days later; a run with no finish stays ``None``."""
    moved: dict[date, date] = {}
    for day in set(results) - {None}:
        later = day
        for _ in range(days):
            later = next_workday(later)
        moved[day] = later
    return [moved[day] if day is not None else None for day in results]


@dataclass
class EpicForecast:
    epic: str
    source: str
    as_of: date
    remaining_items: int
    remaining_real: int
    remaining_points: int
    closed_items: int
    end_date: date | None
    p50: date | None
    p85: date | None
    on_time_probability: float | None
    throughput_mean: float
    history_days: int
    runs: int
    seed: int
    ahead_items: int = 0
    gate_days: int = 0


def epic_forecast(db: Session, today: date, epic: str, *, runs: int = RUNS) -> EpicForecast:
    """``epic``'s forecast: a real milestone at the agents' pace, a simulated epic at its own.

    A real epic counts only its real stories; its source is mixed if open synthetic stories share
    its name. Items without points count as 0 points. A real milestone is forecast after the items
    ahead of it (``items_ahead``), with the seed every real milestone shares that day, so a later
    milestone is never forecast earlier than one before it. Its finishes then move later by its
    ``gate_days``, ``gate_boundaries × gate_gap`` working days; a milestone with nothing left
    to build, here or ahead, is done: it is not simulated, and its P50 and P85 are the day its
    last real story closed (``None`` with no dated closed story), whatever ``today`` is.
    """
    milestone = real_epic(db, epic)
    story_source = "github" if milestone else "synthetic"
    is_story = (Issue.epic == epic, Issue.source == story_source)
    items = list(
        db.scalars(select(Issue).where(*is_story, Issue.state == "open").order_by(Issue.id))
    )
    closed = db.scalar(select(func.count(Issue.id)).where(*is_story, Issue.state == "closed"))
    if milestone:
        samples = real_throughput(db, today=today)
        history_days = len(samples)
        end_date = milestone.due_on
        shared = db.scalars(
            select(Issue.id)
            .where(Issue.epic == epic, Issue.source == "synthetic", Issue.state == "open")
            .limit(1)
        ).first()
        source = "mixed" if shared is not None else "github"
        ahead = items_ahead(db, epic)
        seed = epic_seed("github", today)
        gate_days = gate_boundaries(db, epic) * gate_gap(db) if ahead + len(items) else 0
    else:
        samples = daily_throughput(db, today=today, epic=epic)
        history_days = HISTORY_DAYS
        end_date = None
        source = "synthetic"
        ahead = 0
        seed = epic_seed(epic, today)
        gate_days = 0
    total = ahead + len(items)
    if milestone and not total:
        last_closed = db.scalar(
            select(func.max(Issue.closed_at)).where(*is_story, Issue.state == "closed")
        )
        finished = last_closed.date() if last_closed is not None else None
        p50 = p85 = finished
        on_time = None
        if end_date is not None and finished is not None:
            on_time = 1.0 if finished <= end_date else 0.0
    else:
        results = shift(simulate(total, samples, start=today, runs=runs, seed=seed), gate_days)
        p50 = percentile_date(results, 0.5)
        p85 = percentile_date(results, 0.85)
        on_time = None
        if end_date is not None and runs and (any(samples) or not total):
            on_time = on_time_share(results, end_date, total)
    return EpicForecast(
        epic=epic,
        source=source,
        as_of=today,
        remaining_items=len(items),
        remaining_real=len(items) if milestone else 0,
        remaining_points=sum(item.estimate_points or 0 for item in items),
        closed_items=closed,
        end_date=end_date,
        p50=p50,
        p85=p85,
        on_time_probability=on_time,
        throughput_mean=round(sum(samples) / len(samples), 2) if samples else 0.0,
        history_days=history_days,
        runs=runs,
        seed=seed,
        ahead_items=ahead,
        gate_days=gate_days,
    )


def _short(day: date | None) -> str:
    return f"{day:%b} {day.day}" if day else f"not within {HORIZON} working days"


def describe_epic(forecast: EpicForecast) -> str:
    """One sentence summarising ``forecast``."""
    done = forecast.remaining_items == 0 and forecast.ahead_items == 0
    if forecast.source != "synthetic" and done:
        if forecast.p50 is None:
            return f"{epic_label(forecast.epic)}: no items yet."
        return f"{epic_label(forecast.epic)}: done, finished {_short(forecast.p50)}."
    target = ""
    if forecast.end_date is not None and forecast.on_time_probability is None:
        target = f"; its target is {_short(forecast.end_date)}"
    elif forecast.end_date is not None:
        target = (
            f"; {forecast.on_time_probability:.0%} chance by its target, "
            f"{_short(forecast.end_date)}"
        )
    if forecast.source == "synthetic":
        pace = (
            f"simulated pace: {forecast.throughput_mean:.1f} items per working day over the last "
            f"{forecast.history_days} days"
        )
    else:
        pace = (
            f"agents' pace: {forecast.throughput_mean:.1f} items per working day over the last "
            f"{forecast.history_days} working days"
        )
    ahead = ""
    if forecast.ahead_items > 0:
        ahead = f", after {forecast.ahead_items} items in earlier milestones"
    if forecast.gate_days > 0:
        ahead += f", plus {forecast.gate_days} working day(s) for gates and specs"
    return (
        f"{epic_label(forecast.epic)}: {forecast.remaining_items} items left{ahead}; "
        f"P50 {_short(forecast.p50)}, P85 {_short(forecast.p85)}{target} ({pace})."
    )


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.forecast", description="Forecast the current simulated sprint."
    )
    parser.add_argument("--runs", type=int, default=RUNS, help="simulation runs (default 10000)")
    args = parser.parse_args(argv)
    if args.runs < 1:
        parser.error("--runs must be at least 1")

    with Session(engine or get_engine()) as db:
        forecast = sprint_forecast(db, utcnow().date(), runs=args.runs)
    if forecast is None:
        print("No simulated sprint is in progress.")
        return 0
    print(describe(forecast))
    return 0


if __name__ == "__main__":
    sys.exit(main())
