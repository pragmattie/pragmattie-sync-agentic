import zlib
from datetime import date, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import forecast, synth
from sdlc.db import Base
from sdlc.forecast import (
    HISTORY_DAYS,
    HORIZON,
    EpicForecast,
    current_sprint,
    daily_throughput,
    days_per_point,
    describe,
    describe_epic,
    epic_forecast,
    epic_seed,
    percentile_date,
    real_epics,
    real_throughput,
    seed_for,
    simulate,
    simulated_epics,
    sprint_forecast,
    working_days,
)
from sdlc.tables import Epic, Issue, PullRequest, Sprint

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


def test_days_per_point_gives_floats_from_decimal_sums():
    """MySQL's SUM of an integer column is a Decimal; SQLite's is a number."""

    class DecimalSums:
        def execute(self, query):
            return self

        def all(self):
            return [("leads", Decimal("6.0"), Decimal("4")), ("pipeline", Decimal("3"), 1)]

    rates = days_per_point(DecimalSums(), "synthetic")
    assert rates == {"leads": 1.5, "pipeline": 3.0, None: 1.8}
    assert all(type(rate) is float for rate in rates.values())


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


# Epic forecasts

MILESTONE = "M5 Delivery forecasting"


@pytest.fixture
def epics(session):
    """A real milestone with three open stories and none closed, and a simulated epic.

    Real closures elsewhere: Mon Sep 28 (the first), two on Tue Sep 29, Sat Oct 3 (counts on
    Mon Oct 5) and Tue Oct 6; one today, Wednesday Oct 7, is unfinished.
    """
    milestone = Epic(
        source="github",
        external_id="milestone-5",
        number=5,
        name=MILESTONE,
        due_on=date(2026, 10, 16),
        created_at=datetime(2026, 9, 28, 9),
    )
    session.add_all(
        [
            milestone,
            Epic(
                source="github",
                external_id="milestone-4",
                number=4,
                name="M4 Closed already",
                state="closed",
                created_at=datetime(2026, 9, 28, 9),
            ),
        ]
    )
    for number, closed_at in enumerate(
        [
            datetime(2026, 9, 28, 15),
            datetime(2026, 9, 29, 10),
            datetime(2026, 9, 29, 11),
            datetime(2026, 10, 3, 12),
            datetime(2026, 10, 6, 16),
            datetime(2026, 10, 7, 9),
        ],
        start=1,
    ):
        _closed(session, number, closed_at, source="github", epic="M4 Closed already")
    _issue(session, 10, source="github", epic=MILESTONE, estimate_points=3)
    _issue(session, 11, source="github", epic=MILESTONE)  # no points
    _issue(session, 12, source="github", epic=MILESTONE, estimate_points=2)

    # The simulated epic: one closure on Mon Oct 5 and one on Sat Oct 3 (dropped, as v1),
    # and synthetic closures outside the epic on every working day of the past week.
    _closed(session, 20, datetime(2026, 10, 5, 10), epic="Billing")
    _closed(session, 21, datetime(2026, 10, 3, 10), epic="Billing")
    for k, day in enumerate(working_days(date(2026, 9, 30), date(2026, 10, 6))):
        _closed(session, 30 + k, datetime.combine(day, datetime.min.time()).replace(hour=11))
    _issue(session, 40, epic="Billing", estimate_points=5)
    _issue(session, 41, epic="Billing", estimate_points=3)
    session.flush()
    return milestone


def test_real_and_simulated_epics_are_listed(session, epics):
    assert [epic.name for epic in real_epics(session)] == [MILESTONE]
    assert simulated_epics(session) == ["Billing"]


def test_the_real_pace_starts_at_the_first_real_closure_and_moves_weekends(session, epics):
    # Sep 28 to Oct 6: the Saturday closure counts on Monday Oct 5; today's is unfinished.
    assert real_throughput(session, today=WEDNESDAY) == [1, 2, 0, 0, 0, 1, 1]
    # A shorter window starts at today - days when that is later than the first closure.
    assert real_throughput(session, today=WEDNESDAY, days=3) == [0, 1]


def test_no_real_closure_gives_no_real_pace(session):
    _closed(session, 1, datetime(2026, 10, 5, 10))  # synthetic only
    _issue(session, 2, source="github")
    assert real_throughput(session, today=WEDNESDAY) == []


def test_a_real_milestone_with_no_closed_stories_uses_the_agents_pace(session, epics):
    result = epic_forecast(session, WEDNESDAY, MILESTONE, runs=500)
    assert result.source == "github"
    assert result.as_of == WEDNESDAY
    assert (result.remaining_items, result.remaining_real, result.closed_items) == (3, 3, 0)
    assert result.remaining_points == 5  # the unestimated story counts 0
    assert result.history_days == 7
    assert result.throughput_mean == round(5 / 7, 2)
    assert result.seed == epic_seed(MILESTONE, WEDNESDAY)
    assert result.p50 is not None and WEDNESDAY <= result.p50 <= result.p85
    assert result.end_date == date(2026, 10, 16)
    assert 0.0 < result.on_time_probability <= 1.0


def test_a_real_epic_with_no_real_closure_has_no_dates(session, epics):
    result = epic_forecast(session, date(2026, 9, 28), MILESTONE, runs=50)
    assert result.history_days == 0 and result.throughput_mean == 0.0
    assert result.p50 is None and result.p85 is None
    assert result.on_time_probability == 0.0


def test_a_simulated_epic_uses_only_its_own_pace(session, epics):
    result = epic_forecast(session, WEDNESDAY, "Billing", runs=200)
    samples = daily_throughput(session, today=WEDNESDAY, epic="Billing")
    assert sum(samples) == 1  # the Saturday closure is dropped, as v1
    assert result.source == "synthetic"
    assert (result.remaining_items, result.remaining_real, result.remaining_points) == (2, 0, 8)
    assert result.closed_items == 2
    assert result.history_days == HISTORY_DAYS
    assert result.throughput_mean == round(1 / len(samples), 2)
    assert result.end_date is None and result.on_time_probability is None


def test_a_due_date_gives_an_on_time_probability_and_none_gives_none(session, epics):
    far = epic_forecast(session, WEDNESDAY, MILESTONE, runs=500)
    epics.due_on = WEDNESDAY
    session.flush()
    tight = epic_forecast(session, WEDNESDAY, MILESTONE, runs=500)
    assert tight.end_date == WEDNESDAY
    assert tight.on_time_probability < far.on_time_probability

    epics.due_on = None
    session.flush()
    untargeted = epic_forecast(session, WEDNESDAY, MILESTONE, runs=500)
    assert untargeted.end_date is None and untargeted.on_time_probability is None


def test_adding_a_real_story_pushes_the_forecast_out(session, epics):
    before = epic_forecast(session, WEDNESDAY, MILESTONE, runs=1000)
    _issue(session, 13, source="github", epic=MILESTONE, estimate_points=1)
    after = epic_forecast(session, WEDNESDAY, MILESTONE, runs=1000)
    assert after.seed == before.seed
    assert after.remaining_items == before.remaining_items + 1
    assert after.p50 >= before.p50 and after.p85 >= before.p85
    assert (after.p50, after.p85) != (before.p50, before.p85)
    assert after.on_time_probability <= before.on_time_probability


def test_a_real_epic_sharing_a_simulated_name_is_mixed_and_counts_only_real_stories(session, epics):
    _issue(session, 50, epic=MILESTONE, estimate_points=8)  # synthetic
    result = epic_forecast(session, WEDNESDAY, MILESTONE, runs=50)
    assert result.source == "mixed"
    assert (result.remaining_items, result.remaining_real, result.remaining_points) == (3, 3, 5)


def _epic(**values):
    defaults = dict(
        epic=MILESTONE,
        source="github",
        as_of=WEDNESDAY,
        remaining_items=7,
        remaining_real=7,
        remaining_points=20,
        closed_items=4,
        end_date=date(2026, 10, 15),
        p50=date(2026, 10, 14),
        p85=date(2026, 10, 16),
        on_time_probability=0.72,
        throughput_mean=2.1,
        history_days=10,
        runs=10_000,
        seed=1,
    )
    return EpicForecast(**{**defaults, **values})


def test_describe_epic_for_a_real_epic():
    assert describe_epic(_epic()) == (
        "Delivery forecasting (M5): 7 items left; P50 Oct 14, P85 Oct 16; 72% chance by its "
        "target, Oct 15 (agents' pace: 2.1 items per working day over the last 10 working days)."
    )
    assert describe_epic(_epic(end_date=None, on_time_probability=None, p85=None)) == (
        "Delivery forecasting (M5): 7 items left; P50 Oct 14, P85 not within 260 working days "
        "(agents' pace: 2.1 items per working day over the last 10 working days)."
    )


def test_describe_epic_for_a_simulated_epic():
    simulated = _epic(
        epic="Billing",
        source="synthetic",
        remaining_items=2,
        remaining_real=0,
        end_date=None,
        on_time_probability=None,
        p50=date(2026, 11, 2),
        p85=date(2026, 11, 9),
        throughput_mean=0.25,
        history_days=84,
    )
    assert describe_epic(simulated) == (
        "Billing: 2 items left; P50 Nov 2, P85 Nov 9 "
        "(simulated pace: 0.2 items per working day over the last 84 days)."
    )


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
