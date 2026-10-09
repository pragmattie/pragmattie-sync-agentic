import zlib
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import forecast, synth
from sdlc.db import Base
from sdlc.forecast import (
    HORIZON,
    current_sprint,
    daily_throughput,
    days_per_point,
    describe,
    epic_seed,
    percentile_date,
    seed_for,
    simulate,
    sprint_forecast,
    working_days,
)
from sdlc.tables import Issue, PullRequest, Sprint

EVEN_WEEK_NOW = datetime(2026, 10, 2, 15, 30)  # ISO week 40, a Friday
ODD_WEEK_NOW = datetime(2026, 7, 15, 11, 0)  # ISO week 29, a Wednesday
WEDNESDAY = date(2026, 10, 7)  # ISO week 41
SATURDAY = date(2026, 10, 3)


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _issue(session, number, **values):
    values.setdefault("title", f"Issue {number}")
    values.setdefault("created_at", datetime(2026, 9, 1, 9))
    issue = Issue(number=number, external_id=f"t-{number}", **values)
    session.add(issue)
    session.flush()
    return issue


def _closed(session, number, closed_at, **values):
    return _issue(session, number, state="closed", closed_at=closed_at, **values)


# Dates


def test_working_days_include_both_ends_and_skip_weekends():
    assert working_days(date(2026, 10, 2), date(2026, 10, 5)) == [
        date(2026, 10, 2),
        date(2026, 10, 5),
    ]
    assert working_days(SATURDAY, SATURDAY) == []
    assert forecast.next_workday(date(2026, 10, 2)) == date(2026, 10, 5)
    assert forecast.next_workday(WEDNESDAY) == date(2026, 10, 8)
    assert forecast._first_workday(SATURDAY) == date(2026, 10, 5)
    assert forecast._first_workday(WEDNESDAY) == WEDNESDAY


# Simulation


def test_the_same_seed_gives_identical_results():
    samples = [0, 1, 2, 3, 1, 0, 4]
    first = simulate(20, samples, start=WEDNESDAY, runs=500, seed=42)
    assert first == simulate(20, samples, start=WEDNESDAY, runs=500, seed=42)
    assert first != simulate(20, samples, start=WEDNESDAY, runs=500, seed=43)


@pytest.mark.parametrize("start", [WEDNESDAY, SATURDAY], ids=["weekday", "weekend"])
def test_nothing_remaining_finishes_on_the_first_working_day(start):
    expected = forecast._first_workday(start)
    assert simulate(0, [1, 2], start=start, runs=5, seed=1) == [expected] * 5
    assert simulate(0, [], start=start, runs=3, seed=1) == [expected] * 3


@pytest.mark.parametrize("samples", [[], [0, 0, 0]], ids=["none", "zeros"])
def test_no_throughput_never_finishes(samples):
    assert simulate(3, samples, start=WEDNESDAY, runs=10, seed=1) == [None] * 10


def test_the_horizon_is_respected():
    assert simulate(HORIZON + 1, [1], start=WEDNESDAY, runs=4, seed=1) == [None] * 4
    last = working_days(WEDNESDAY, WEDNESDAY + timedelta(days=HORIZON * 2))[HORIZON - 1]
    assert simulate(HORIZON, [1], start=WEDNESDAY, runs=4, seed=1) == [last] * 4


def test_runs_finish_on_working_days_only():
    for day in simulate(7, [0, 1, 2], start=SATURDAY, runs=200, seed=9):
        assert day is not None and forecast.is_workday(day) and day > SATURDAY


def test_percentile_date_sorts_none_last_and_handles_the_edges():
    days = [date(2026, 10, d) for d in (9, 5, 7, 6, 8)]
    assert percentile_date([], 0.5) is None
    assert percentile_date(days, 0.5) == date(2026, 10, 7)  # index ceil(2.5) - 1 = 2
    assert percentile_date(days, 0.85) == date(2026, 10, 9)  # index ceil(4.25) - 1 = 4
    assert percentile_date(days, 1.0) == date(2026, 10, 9)
    assert percentile_date(days, 0.0) == date(2026, 10, 5)
    with_none = [None, date(2026, 10, 6), None, date(2026, 10, 5)]
    assert percentile_date(with_none, 0.5) == date(2026, 10, 6)
    assert percentile_date(with_none, 0.85) is None
    assert percentile_date([None, None], 0.5) is None


def test_seeds_are_crc32_of_the_documented_strings():
    sprint = Sprint(id=4, source="synthetic", name="Sprint 4")
    assert seed_for(sprint, WEDNESDAY) == zlib.crc32(b"synthetic:4:2026-10-07")
    assert epic_seed("Billing", WEDNESDAY) == zlib.crc32(b"epic:Billing:2026-10-07")
    assert seed_for(sprint, WEDNESDAY) != seed_for(sprint, WEDNESDAY + timedelta(days=1))


# Throughput


def test_daily_throughput_excludes_today_and_weekends_and_filters(session):
    _closed(session, 1, datetime(2026, 10, 6, 10))
    _closed(session, 2, datetime(2026, 10, 6, 16))
    _closed(session, 3, datetime(2026, 10, 7, 9))  # today: unfinished
    _closed(session, 4, datetime(2026, 10, 3, 12))  # a Saturday
    _closed(session, 5, datetime(2026, 10, 5, 11), source="github")
    _closed(session, 6, datetime(2026, 10, 2, 11), epic="Forecast")
    _closed(session, 7, datetime(2026, 9, 29, 11))  # before the window
    _issue(session, 8)  # open

    # Window Sep 30 to Oct 6: Wed, Thu, Fri, Mon, Tue.
    assert daily_throughput(session, today=WEDNESDAY, days=7) == [0, 0, 1, 1, 2]
    assert daily_throughput(session, today=WEDNESDAY, source="synthetic", days=7) == [
        0,
        0,
        1,
        0,
        2,
    ]
    assert daily_throughput(session, today=WEDNESDAY, source="github", days=7) == [0, 0, 0, 1, 0]
    assert daily_throughput(session, today=WEDNESDAY, epic="Forecast", days=7) == [0, 0, 1, 0, 0]
    assert (
        daily_throughput(session, today=WEDNESDAY, source="github", epic="Forecast", days=7)
        == [0] * 5
    )


def test_daily_throughput_defaults_to_the_history_window(session):
    samples = daily_throughput(session, today=WEDNESDAY)
    assert len(samples) == len(
        working_days(WEDNESDAY - timedelta(days=84), WEDNESDAY - timedelta(days=1))
    )
    assert set(samples) == {0}


# Items at risk


@pytest.fixture
def sprint_with_risks(session):
    """A sprint ending Sunday Oct 11; on Wednesday Oct 7 three working days are left."""
    sprint = Sprint(
        name="Sprint 9",
        start_date=date(2026, 9, 28),
        end_date=date(2026, 10, 11),
        source="synthetic",
    )
    session.add(sprint)
    session.flush()
    # Rates: forecasting 2.0 days a point, leads 1.0, overall 6 / 4 = 1.5.
    _closed(
        session, 1, datetime(2026, 9, 10), module="forecasting", estimate_points=2, actual_days=4.0
    )
    _closed(session, 2, datetime(2026, 9, 11), module="leads", estimate_points=2, actual_days=2.0)
    _closed(session, 3, datetime(2026, 9, 12), module="leads", actual_days=9.0)  # unestimated
    _closed(
        session,
        4,
        datetime(2026, 9, 12),
        module="leads",
        estimate_points=3,
        source="github",
        actual_days=30.0,
    )

    def open_item(number, started=None, **values):
        issue = _issue(session, number, sprint_id=sprint.id, **values)
        if started:
            for k, opened in enumerate((started, started + timedelta(days=1))):
                session.add(
                    PullRequest(
                        number=number * 10 + k,
                        external_id=f"pr-{number}-{k}",
                        title=issue.title,
                        issue_id=issue.id,
                        created_at=datetime.combine(opened, datetime.min.time()).replace(hour=10),
                    )
                )
        return issue

    open_item(10, started=date(2026, 9, 28), module="forecasting", estimate_points=1)  # overrun
    open_item(11, module="forecasting", estimate_points=3)  # not started, 6.0 left
    open_item(12, started=date(2026, 10, 6), estimate_points=4)  # no module, 5.0 left
    open_item(13, started=date(2026, 10, 6), module="leads", estimate_points=1)  # fits
    open_item(14, module="leads", estimate_points=1)  # not started, fits
    open_item(15, started=date(2026, 9, 28), module="leads")  # no points, overrun
    _issue(session, 16, module="forecasting", estimate_points=8)  # not in the sprint
    session.flush()
    return sprint


def test_days_per_point_is_per_module_with_an_overall_rate(session, sprint_with_risks):
    assert days_per_point(session, "synthetic") == {"forecasting": 2.0, "leads": 1.0, None: 1.5}
    assert days_per_point(session, "github") == {"leads": 10.0, None: 10.0}


def test_items_at_risk_are_flagged_with_v1_reasons_worst_first(session, sprint_with_risks):
    result = sprint_forecast(session, WEDNESDAY, sprint=sprint_with_risks, runs=50)

    assert result.working_days_left == 3
    assert [risk.number for risk in result.at_risk] == [11, 12, 10, 15]
    reasons = {risk.number: risk.reason for risk in result.at_risk}
    assert reasons[11] == (
        "3 pt, not started, about 6.0 working days of work left but 3 left in the sprint "
        "(forecasting work has taken 2.0 working days per point)."
    )
    assert reasons[12] == (
        "4 pt, started Oct 06, about 5.0 working days of work left but 3 left in the sprint "
        "(unassigned work has taken 1.5 working days per point)."
    )
    assert reasons[10] == (
        "In progress 7 working days against about 2.0 expected "
        "(1 pt; forecasting work has taken 2.0 working days per point): well over, still open."
    )
    assert reasons[15] == (
        "In progress 7 working days against about 0.0 expected "
        "(0 pt; leads work has taken 1.0 working days per point): well over, still open."
    )

    overrun = next(risk for risk in result.at_risk if risk.number == 10)
    assert overrun.started == date(2026, 9, 28)
    assert overrun.expected_days == 2.0
    assert overrun.remaining_days == 0.0
    not_started = next(risk for risk in result.at_risk if risk.number == 11)
    assert not_started.started is None
    assert not_started.remaining_days == 6.0
    assert not_started.points == 3 and not_started.module == "forecasting"


def test_an_item_just_under_the_overrun_bar_is_not_flagged(session, sprint_with_risks):
    # Item 10 expects 2.0 days, so it overruns at max(3.0, 4.0) = 4 elapsed working days.
    on_day_four = sprint_forecast(session, date(2026, 10, 2), sprint=sprint_with_risks, runs=10)
    on_day_three = sprint_forecast(session, date(2026, 10, 1), sprint=sprint_with_risks, runs=10)
    assert 10 in [risk.number for risk in on_day_four.at_risk]
    assert 10 not in [risk.number for risk in on_day_three.at_risk]


def test_sprint_forecast_counts_the_sprints_open_items(session, sprint_with_risks):
    result = sprint_forecast(session, WEDNESDAY, sprint=sprint_with_risks, runs=50)
    assert result.sprint == "Sprint 9"
    assert result.source == "synthetic"
    assert result.remaining_items == 6
    assert result.remaining_points == 10
    # Three synthetic closures in the window, so some runs finish and none before today.
    assert result.throughput_mean > 0
    assert result.p50 is not None and result.p50 >= WEDNESDAY


# Sprint forecast on a generated history


@pytest.fixture
def built(session):
    synth.build(session, now=EVEN_WEEK_NOW)
    session.commit()
    return session


def test_the_same_day_repeats_exactly_and_a_new_day_changes_the_seed(built):
    today = EVEN_WEEK_NOW.date()
    first = sprint_forecast(built, today, runs=1000)
    again = sprint_forecast(built, today, runs=1000)
    assert first is not None
    assert first == again

    tomorrow = sprint_forecast(built, today + timedelta(days=1), runs=1000)
    assert tomorrow.sprint == first.sprint
    assert tomorrow.seed != first.seed
    assert tomorrow != first


def test_the_generated_sprint_forecast_has_sensible_fields(built):
    today = EVEN_WEEK_NOW.date()
    result = sprint_forecast(built, today, runs=1000)
    sprint = current_sprint(built, today, "synthetic")

    assert result.seed == seed_for(sprint, today)
    assert (result.start_date, result.end_date, result.as_of) == (
        sprint.start_date,
        sprint.end_date,
        today,
    )
    open_items = built.scalars(
        select(Issue).where(Issue.sprint_id == sprint.id, Issue.state == "open")
    ).all()
    assert result.remaining_items == len(open_items) > 0
    assert result.working_days_left == len(working_days(today, sprint.end_date))
    assert result.history_days == 84 and result.runs == 1000
    assert result.throughput_mean > 0
    assert 0.0 <= result.on_time_probability <= 1.0
    assert result.p50 is not None and result.p50 <= result.p85
    shortfalls = [risk.remaining_days - result.working_days_left for risk in result.at_risk]
    assert shortfalls == sorted(shortfalls, reverse=True)


@pytest.mark.parametrize("now", [EVEN_WEEK_NOW, ODD_WEEK_NOW], ids=["even-week", "odd-week"])
def test_the_current_sprint_resolves_in_both_iso_week_parities(session, now):
    synth.build(session, now=now)
    session.commit()
    last = current_sprint(session, now.date(), "synthetic")
    assert last is not None
    # The Wednesday of each week of the fortnight: one even ISO week, one odd.
    wednesdays = [last.start_date + timedelta(days=2), last.start_date + timedelta(days=9)]
    assert {day.isocalendar().week % 2 for day in wednesdays} == {0, 1}
    for today in wednesdays:
        assert current_sprint(session, today, "synthetic").id == last.id, today
        result = sprint_forecast(session, today, runs=100)
        assert result is not None and result.sprint == last.name


def test_no_sprint_in_progress_gives_none(session):
    assert sprint_forecast(session, WEDNESDAY, runs=10) is None
    synth.build(session, now=EVEN_WEEK_NOW)
    session.commit()
    assert current_sprint(session, WEDNESDAY, "github") is None
    assert sprint_forecast(session, WEDNESDAY, source="github", runs=10) is None
    assert sprint_forecast(session, date(2027, 6, 1), runs=10) is None


# Summary and command line


def test_describe_summarises_the_forecast(session, sprint_with_risks):
    text = describe(sprint_forecast(session, WEDNESDAY, sprint=sprint_with_risks, runs=50))
    assert text.startswith("Sprint 9 (synthetic), Sep 28 to Oct 11, as of Wed Oct 07")
    assert "6 items (10 pt), 3 working days left" in text
    assert "50 runs" in text
    assert "#11 Issue 11: 3 pt, not started" in text


def test_main_prints_the_current_simulated_sprint(sqlite_engine, monkeypatch, capsys):
    Base.metadata.create_all(sqlite_engine)
    monkeypatch.setattr(forecast, "utcnow", lambda: EVEN_WEEK_NOW)
    assert forecast.main(["--runs", "200"], engine=sqlite_engine) == 0
    assert "No simulated sprint is in progress." in capsys.readouterr().out

    with Session(sqlite_engine) as session:
        synth.build(session, now=EVEN_WEEK_NOW)
        session.commit()
    assert forecast.main(["--runs", "200"], engine=sqlite_engine) == 0
    out = capsys.readouterr().out
    assert "Sprint 13 (synthetic)" in out
    assert "200 runs" in out
    assert "P85: " in out
