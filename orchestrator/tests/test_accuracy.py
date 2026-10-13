import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from sdlc import accuracy, synth
from sdlc.db import Base
from sdlc.suite_selector import RESULT
from sdlc.tables import AgentDecision, Epic, Forecast, Issue, Sprint
from sdlc.tiers import load_policy

GENERATED_NOW = datetime(2026, 9, 16, 12, 0)
RUNS = 200  # fewer than the report's runs, for speed


@pytest.fixture(autouse=True)
def empty_cache():
    accuracy._cache.clear()
    yield
    accuracy._cache.clear()


@pytest.fixture(scope="module")
def history():
    """One generated history, shared; a test that changes it rolls back."""
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        synth.build(db, now=GENERATED_NOW)
        db.commit()
        yield db
    engine.dispose()


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


# Delivery forecasts, simulated


def test_the_backtest_grades_past_sprints_from_what_was_known_then(history):
    result = accuracy.forecast_accuracy(history, GENERATED_NOW.date(), runs=RUNS)

    assert result["simulated"] is True
    assert result["as_of_workday"] == 3
    assert result["targets"] == accuracy.TARGETS
    assert result["graded"] == len(result["sprints"]) >= 3
    for row in result["sprints"]:
        assert row["as_of"] < row["end_date"] < GENERATED_NOW.date()
        assert row["p50"] <= row["p85"]
        if row["p50_met"]:
            assert row["p85_met"]
    last = result["sprints"][-1]
    assert last["p50_hit_rate_so_far"] == result["p50_hit_rate"]
    assert last["p85_hit_rate_so_far"] == result["p85_hit_rate"]


def test_a_sprint_too_early_in_the_history_is_skipped(history):
    first = history.scalars(
        select(Sprint).where(Sprint.source == "synthetic").order_by(Sprint.start_date)
    ).first()

    assert accuracy.backtest_sprint(history, first, runs=RUNS) == accuracy.TOO_EARLY


def test_closing_a_sprints_items_later_does_not_change_its_forecast(history):
    result = accuracy.forecast_accuracy(history, GENERATED_NOW.date(), runs=RUNS)
    row = next(row for row in result["sprints"] if row["items"])
    sprint = history.scalars(select(Sprint).where(Sprint.name == row["sprint"])).one()
    as_of = datetime.combine(row["as_of"], datetime.min.time())
    try:
        late = history.scalars(
            select(Issue).where(Issue.sprint_id == sprint.id, Issue.closed_at >= as_of)
        ).first()
        late.closed_at = datetime(2026, 9, 15, 15)  # the day before GENERATED_NOW
        history.add(
            Issue(
                title="Added after the forecast",
                sprint_id=sprint.id,
                created_at=as_of + timedelta(days=1),
                closed_at=as_of + timedelta(days=2),
                state="closed",
            )
        )
        history.flush()

        again = accuracy.backtest_sprint(history, sprint, runs=RUNS)
    finally:
        history.rollback()

    assert (again["as_of"], again["items"], again["p50"], again["p85"]) == (
        row["as_of"],
        row["items"],
        row["p50"],
        row["p85"],
    )
    assert again["actual"] == date(2026, 9, 15)


# Risk score, simulated


def test_risk_counts_caught_incident_prs_by_sprint(history):
    result = accuracy.risk_accuracy(history, load_policy())

    assert result["simulated"] is True
    assert result["flagged_from"] == "T2"
    sprints = history.scalars(select(Sprint).where(Sprint.source == "synthetic")).all()
    assert len(result["sprints"]) == len(sprints)
    assert result["incident_prs"] == sum(row["incident_prs"] for row in result["sprints"])
    assert result["caught"] == sum(row["caught"] for row in result["sprints"])
    assert 0 < result["caught"] <= result["incident_prs"]
    assert result["recall"] == round(result["caught"] / result["incident_prs"], 3)
    assert result["sprints"][-1]["recall_so_far"] == result["recall"]
    assert result["real"] == {"merged_prs": 0, "incident_prs": 0}


# Triage, real


def _issue(db, number, **fields):
    defaults = {"module": "leads", "type": "feature", "priority": "p2", "estimate_points": 3}
    db.add(
        Issue(
            source="github",
            number=number,
            title=f"Issue {number}",
            created_at=datetime(2026, 9, 1, 9),
            **{**defaults, **fields},
        )
    )


def _triage(db, number, at, *, trigger="opened", status="ok", source="github", **said):
    output = {"module": "leads", "type": "feature", "priority": "p2", "estimate_points": 3}
    db.add(
        AgentDecision(
            created_at=at,
            agent="triage",
            agent_version="v1",
            subject_type="issue",
            subject_source=source,
            subject_id=number,
            head_sha=f"v{at:%d%H}",
            trigger=trigger,
            status=status,
            output={**output, **said},
        )
    )


def test_triage_counts_labels_a_person_corrected_afterwards(session):
    _issue(session, 1)
    _issue(session, 2, module="accounts", estimate_points=5)
    _triage(session, 1, datetime(2026, 9, 8, 10))  # a Tuesday; kept
    _triage(session, 2, datetime(2026, 9, 9, 10), module="accounts", estimate_points=5)
    _triage(session, 2, datetime(2026, 9, 15, 10))  # newest: since corrected by a person
    _triage(session, 1, datetime(2026, 9, 16, 10), trigger="trial", module="billing_auth")
    _triage(session, 3, datetime(2026, 9, 16, 11), status="failed")
    _triage(session, 4, datetime(2026, 9, 16, 12), source="synthetic")
    session.commit()

    result = accuracy.triage_accuracy(session)

    assert result["simulated"] is False
    assert (result["total"], result["corrected"], result["kept_rate"]) == (2, 1, 0.5)
    assert result["enough"] is False
    unchanged = {"module_changed": 0, "type_changed": 0, "priority_changed": 0}
    assert result["weeks"] == [
        {"week": date(2026, 9, 7), "labelled": 1, "corrected": 0, **unchanged, "points_changed": 0},
        {
            "week": date(2026, 9, 14),
            "labelled": 1,
            "corrected": 1,
            **unchanged,
            "module_changed": 1,
            "points_changed": 1,
        },
    ]


def test_an_unset_value_on_the_issue_is_not_a_correction(session):
    _issue(session, 1, priority=None)
    _triage(session, 1, datetime(2026, 9, 8, 10), priority="p1")
    session.commit()

    assert accuracy.triage_accuracy(session)["corrected"] == 0


# Test selector, real


def _selector(db, number, status, *, trigger=RESULT, source="github"):
    db.add(
        AgentDecision(
            created_at=datetime(2026, 9, 7 + number, 10),
            agent="test_selector",
            agent_version="v1",
            subject_type="pr",
            subject_source=source,
            subject_id=number,
            head_sha=f"sha{number}",
            trigger=trigger,
            status=status,
        )
    )


def test_selector_counts_settled_results_that_missed_a_failure(session):
    _selector(session, 1, "ok")
    _selector(session, 2, "missed")
    _selector(session, 7, "ok")  # the next week
    _selector(session, 3, "ok", trigger="push")  # a recommendation, not a result
    _selector(session, 4, "missed", source="synthetic")
    session.commit()

    result = accuracy.selector_accuracy(session)

    assert result["simulated"] is False
    assert (result["total"], result["missed"], result["miss_rate"]) == (3, 1, 0.333)
    assert result["enough"] is False
    assert result["weeks"] == [
        {"week": date(2026, 9, 7), "settled": 2, "missed": 1},
        {"week": date(2026, 9, 14), "settled": 1, "missed": 0},
    ]


# Milestone forecasts, real


def _milestone(db, name, *, due_on=None, closed=(), open_items=0, source="github"):
    db.add(Epic(source=source, name=name, due_on=due_on, created_at=datetime(2026, 8, 31)))
    for day in closed:
        db.add(
            Issue(
                source=source,
                title=f"{name} item",
                epic=name,
                state="closed",
                created_at=datetime(2026, 8, 31),
                closed_at=datetime(2026, 9, day, 16),
            )
        )
    for _ in range(open_items):
        db.add(
            Issue(source=source, title=f"{name} item", epic=name, created_at=datetime(2026, 8, 31))
        )


def _forecast(db, name, created_day, p50, p85):
    db.add(
        Forecast(
            created_at=datetime(2026, 9, created_day, 9),
            as_of=date(2026, 9, created_day),
            kind="epic",
            subject=name,
            source="github",
            trigger="schedule",
            inputs_hash="x",
            remaining_items=2,
            remaining_points=5,
            p50=p50,
            p85=p85,
            throughput_mean=1.0,
            history_days=10,
            runs=100,
            seed=1,
        )
    )


def test_milestones_grade_the_first_forecast_against_when_the_work_finished(session):
    _milestone(session, "M1 Foundations", due_on=date(2026, 9, 30), closed=(3, 8))
    _forecast(session, "M1 Foundations", 1, date(2026, 9, 10), date(2026, 9, 15))
    _forecast(session, "M1 Foundations", 5, date(2026, 9, 20), date(2026, 9, 25))  # not graded
    _milestone(session, "M2 Leads", due_on=date(2026, 9, 11), closed=(4, 16))
    _forecast(session, "M2 Leads", 1, date(2026, 9, 10), date(2026, 9, 14))
    _milestone(session, "M10 Later", closed=(3,), open_items=1)
    _forecast(session, "M10 Later", 1, date(2026, 9, 10), date(2026, 9, 14))
    _milestone(session, "M3 Accounts", closed=(2,))
    _forecast(session, "M3 Accounts", 5, date(2026, 9, 10), date(2026, 9, 14))
    _milestone(session, "M4 Unforecast", closed=(2,))
    _milestone(session, "Simulated epic", closed=(2,), source="synthetic")
    _forecast(session, "Simulated epic", 1, date(2026, 9, 10), date(2026, 9, 14))
    session.commit()

    result = accuracy.milestone_accuracy(session)

    assert result["simulated"] is False
    rows = result["milestones"]
    assert [row["milestone"] for row in rows] == [
        "M1 Foundations",
        "M2 Leads",
        "M3 Accounts",
        "M10 Later",
    ]
    met, late, too_late, still_open = rows
    assert met == {
        "milestone": "M1 Foundations",
        "label": "Foundations (M1)",
        "due_on": date(2026, 9, 30),
        "first_forecast_at": datetime(2026, 9, 1, 9),
        "p50": date(2026, 9, 10),
        "p85": date(2026, 9, 15),
        "actual": date(2026, 9, 8),
        "p50_met": True,
        "p85_met": True,
        "p50_error_days": -2,
        "target_met": True,
        "graded": True,
        "reason": None,
    }
    assert (late["actual"], late["p50_met"], late["p85_met"]) == (date(2026, 9, 16), False, False)
    assert (late["p50_error_days"], late["target_met"]) == (4, False)
    assert (too_late["graded"], too_late["reason"]) == (False, accuracy.FORECAST_TOO_LATE)
    assert too_late["p50_met"] is None and too_late["target_met"] is None
    assert (still_open["graded"], still_open["reason"]) == (False, accuracy.STILL_OPEN)
    assert still_open["actual"] is None
    assert (result["total"], result["enough"]) == (2, False)
    assert (result["p50_hit_rate"], result["p85_hit_rate"]) == (0.5, 0.5)


def test_workday_error_skips_weekends():
    friday, monday = date(2026, 9, 11), date(2026, 9, 14)

    assert accuracy.workday_error(monday, friday) == 1
    assert accuracy.workday_error(friday, monday) == -1
    assert accuracy.workday_error(friday, friday) == 0


# The report


def test_the_report_marks_each_trend_and_caches_the_simulated_ones(history, monkeypatch):
    monkeypatch.setattr(accuracy, "RUNS", RUNS)
    today = GENERATED_NOW.date()

    first = accuracy.report(history, today)
    second = accuracy.report(history, today)

    assert set(first) == {
        "forecast",
        "risk",
        "triage",
        "test_selector",
        "milestones",
        "min_points",
        "generated_at",
    }
    assert first["forecast"]["simulated"] and first["risk"]["simulated"]
    assert not any(first[key]["simulated"] for key in ("triage", "test_selector", "milestones"))
    assert first["min_points"] == accuracy.MIN_POINTS
    assert first["generated_at"].microsecond == 0
    assert second["forecast"] is first["forecast"]
    assert second["risk"] is first["risk"]
    assert second["triage"] is not first["triage"]

    tomorrow = accuracy.report(history, today + timedelta(days=1))
    assert tomorrow["forecast"] is not first["forecast"]
    assert len(accuracy._cache) == 1


def test_the_cli_prints_the_report_as_json(sqlite_engine, capsys):
    Base.metadata.create_all(sqlite_engine)

    assert accuracy.main([], engine=sqlite_engine) == 0

    printed = json.loads(capsys.readouterr().out)
    assert printed["forecast"]["graded"] == 0
    assert printed["milestones"]["milestones"] == []


def test_the_endpoint_returns_the_report(client, sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        _milestone(db, "M1 Foundations", due_on=date(2026, 9, 30), closed=(3, 8))
        _forecast(db, "M1 Foundations", 1, date(2026, 9, 10), date(2026, 9, 15))
        _selector(db, 1, "missed")
        db.commit()

    response = client.get("/api/v1/signals/accuracy")

    assert response.status_code == 200
    body = response.json()
    assert body["forecast"]["simulated"] is True and body["risk"]["simulated"] is True
    assert body["test_selector"]["missed"] == 1
    assert body["milestones"]["milestones"][0]["actual"] == "2026-09-08"
    assert body["milestones"]["total"] == 1
    assert client.post("/api/v1/signals/accuracy").status_code == 405
