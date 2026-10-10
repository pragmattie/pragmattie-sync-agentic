import json
import os
from datetime import date, datetime, timedelta

import pytest
from alembic import command
from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from sdlc import forecaster, synth
from sdlc.audit import record_decision
from sdlc.config import get_settings
from sdlc.db import Base
from sdlc.forecast import current_sprint
from sdlc.forecaster import (
    AGENT,
    TRAIL,
    ForecastRunner,
    _fingerprint,
    dashboard,
    latest,
    moved,
    serialize,
    sprint_inputs,
)
from sdlc.tables import AgentDecision, Forecast, Issue, PullRequest, Sprint
from tests.test_migrations import _alembic_config

WEDNESDAY = datetime(2026, 10, 7, 10, 0)  # inside the simulated Sprint 13
RUNS = 200


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        synth.build(db, now=WEDNESDAY)
        db.commit()
    return sqlite_engine


@pytest.fixture
def runner(engine):
    return ForecastRunner("shadow", engine=engine, runs=RUNS)


def _counts(engine):
    with Session(engine) as db:
        return (
            db.scalar(select(func.count(Forecast.id))),
            db.scalar(select(func.count(AgentDecision.id)).where(AgentDecision.agent == AGENT)),
        )


def _triggers(engine):
    with Session(engine) as db:
        return list(db.scalars(select(Forecast.trigger).order_by(Forecast.id)))


def _sprint(db):
    return current_sprint(db, WEDNESDAY.date(), "synthetic")


def _open_items(db):
    sprint = _sprint(db)
    query = select(Issue).where(Issue.sprint_id == sprint.id, Issue.state == "open")
    return list(db.scalars(query.order_by(Issue.id)))


# Fingerprints


def test_the_fingerprint_ignores_row_order_and_depends_on_day_and_subject():
    rows = [{"id": 1, "points": 3}, {"id": 2, "points": 5}]
    day = date(2026, 10, 7)
    assert _fingerprint(day, "Sprint 13", rows) == _fingerprint(day, "Sprint 13", rows[::-1])
    assert len(_fingerprint(day, "Sprint 13", rows)) == 64
    assert _fingerprint(day, "Sprint 13", rows) != _fingerprint(day, "Sprint 12", rows)
    assert _fingerprint(day, "Sprint 13", rows) != _fingerprint(
        day + timedelta(days=1), "Sprint 13", rows
    )


def test_sprint_inputs_list_each_open_item_and_its_first_pull_request(engine):
    with Session(engine) as db:
        inputs = sprint_inputs(db, _sprint(db))
        items = _open_items(db)
        assert [row["id"] for row in sorted(inputs, key=lambda row: row["id"])] == [
            item.id for item in items
        ]
        first = db.scalar(
            select(func.min(PullRequest.created_at)).where(PullRequest.issue_id == items[0].id)
        )
        row = next(row for row in inputs if row["id"] == items[0].id)
        assert row == {
            "id": items[0].id,
            "points": items[0].estimate_points,
            "module": items[0].module,
            "started": first.isoformat() if first else None,
        }


# Polling


def test_a_forecast_is_saved_only_when_its_inputs_change(engine, runner):
    assert runner.poll_once(WEDNESDAY) == {"mode": "shadow", "assessed": 1, "unchanged": 0}
    assert _counts(engine) == (1, 1)
    assert _triggers(engine) == ["schedule"]

    later = WEDNESDAY + timedelta(hours=3)
    assert runner.poll_once(later) == {"mode": "shadow", "assessed": 0, "unchanged": 1}
    assert _counts(engine) == (1, 1)

    with Session(engine) as db:
        item = _open_items(db)[0]
        item.state = "closed"
        item.closed_at = later
        db.commit()
    assert runner.poll_once(later)["assessed"] == 1
    assert _counts(engine) == (2, 2)
    assert _triggers(engine) == ["schedule", "change"]

    tomorrow = WEDNESDAY + timedelta(days=1)
    assert runner.poll_once(tomorrow)["assessed"] == 1
    assert _counts(engine) == (3, 3)
    assert runner.poll_once(tomorrow)["unchanged"] == 1

    assert runner.poll_once(tomorrow, force=True)["assessed"] == 1
    assert _counts(engine) == (4, 4)
    assert _triggers(engine) == ["schedule", "change", "schedule", "manual"]


@pytest.mark.parametrize("change", ["re-estimate", "start", "add"])
def test_re_estimating_starting_or_adding_an_item_is_a_change(engine, runner, change):
    runner.poll_once(WEDNESDAY)
    with Session(engine) as db:
        item = _open_items(db)[0]
        if change == "re-estimate":
            item.estimate_points = (item.estimate_points or 0) + 1
        elif change == "start":
            db.add(
                PullRequest(
                    title="Earlier start",
                    issue_id=item.id,
                    created_at=datetime(2026, 9, 28, 9),
                    external_id="t-start",
                )
            )
        else:
            db.add(
                Issue(
                    title="Added mid-sprint",
                    sprint_id=item.sprint_id,
                    estimate_points=2,
                    created_at=WEDNESDAY,
                    external_id="t-added",
                )
            )
        db.commit()
    assert runner.poll_once(WEDNESDAY + timedelta(minutes=5))["assessed"] == 1
    assert _triggers(engine) == ["schedule", "change"]


def test_the_saved_row_and_its_audit_row(engine, runner):
    runner.poll_once(WEDNESDAY)
    with Session(engine) as db:
        row = latest(db, "sprint", "Sprint 13")
        decision = db.scalars(select(AgentDecision)).one()

        assert row.created_at == WEDNESDAY
        assert row.as_of == WEDNESDAY.date()
        assert (row.kind, row.source, row.subject) == ("sprint", "synthetic", "Sprint 13")
        assert row.end_date == date(2026, 10, 11)
        assert row.runs == RUNS
        assert row.remaining_items == len(_open_items(db))
        assert row.remaining_real == 0
        assert isinstance(row.at_risk, list)

        assert decision.agent == "forecaster" and decision.agent_version == "v1"
        assert (decision.subject_type, decision.subject_source) == ("sprint", "synthetic")
        assert decision.subject_id == row.id
        assert decision.head_sha == row.inputs_hash[:40]
        assert decision.trigger == "schedule"
        assert decision.status == "ok"
        assert decision.created_at == WEDNESDAY
        assert decision.model_id is None and decision.input_tokens is None
        assert decision.inputs_digest == {
            "remaining_items": row.remaining_items,
            "remaining_points": row.remaining_points,
            "throughput_mean": row.throughput_mean,
            "history_days": row.history_days,
        }
        assert decision.output["subject"] == "Sprint 13"
        assert decision.output["p50"] == (row.p50.isoformat() if row.p50 else None)
        assert decision.output["p85"] == (row.p85.isoformat() if row.p85 else None)
        assert decision.output["end_date"] == "2026-10-11"
        assert decision.output["confidence"] == row.on_time_probability
        assert decision.output["rationale"].startswith("Sprint 13 (synthetic)")
        assert (decision.output["runs"], decision.output["seed"]) == (RUNS, row.seed)
        assert decision.action_taken == {"saved_forecast": row.id}


def test_off_does_nothing(engine):
    assert ForecastRunner("off", engine=engine).poll_once(WEDNESDAY, force=True) == {"mode": "off"}
    assert _counts(engine) == (0, 0)


def test_no_sprint_in_progress_saves_nothing(engine, runner):
    weekend_after = datetime(2027, 1, 1, 9)  # long after the last simulated sprint
    assert runner.poll_once(weekend_after) == {"mode": "shadow", "assessed": 0, "unchanged": 0}
    assert _counts(engine) == (0, 0)


def test_cli_once_and_now(engine, monkeypatch, capsys):
    monkeypatch.setenv("ORCHESTRATOR_MODE", "shadow")
    monkeypatch.setattr(forecaster, "utcnow", lambda: WEDNESDAY)
    real_forecast = forecaster.sprint_forecast
    monkeypatch.setattr(
        forecaster,
        "sprint_forecast",
        lambda *args, **kwargs: real_forecast(*args, **{**kwargs, "runs": RUNS}),
    )
    forecaster.get_settings.cache_clear()
    try:
        assert forecaster.main(["once"], engine=engine) == 0
        assert forecaster.main(["once"], engine=engine) == 0
        assert forecaster.main(["now"], engine=engine) == 0
    finally:
        forecaster.get_settings.cache_clear()
    printed = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["assessed"] for line in printed] == [1, 0, 1]
    assert _triggers(engine) == ["schedule", "manual"]


# moved and the dashboard


def _forecast(db, kind, subject, day, p50, p85, *, source="synthetic", hour=9):
    row = Forecast(
        created_at=datetime.combine(day, datetime.min.time()) + timedelta(hours=hour),
        as_of=day,
        kind=kind,
        subject=subject,
        source=source,
        trigger="schedule",
        inputs_hash=f"{subject}-{day}-{hour}".ljust(64, "0")[:64],
        remaining_items=3,
        remaining_points=8,
        p50=p50,
        p85=p85,
        on_time_probability=0.5,
        throughput_mean=1.2,
        history_days=84,
        runs=RUNS,
        seed=2**32 - 1,
        at_risk=[],
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        yield db


def test_moved_with_a_single_forecast_has_no_previous(db):
    only = _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), date(2026, 10, 9), None)
    assert moved(db, "sprint", "Sprint 1") == {
        "latest": serialize(only),
        "previous": None,
        "p50_moved_days": None,
        "p85_moved_days": None,
    }
    assert moved(db, "sprint", "Sprint 2") is None


def test_moved_counts_the_days_p50_and_p85_moved(db):
    _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), date(2026, 10, 9), date(2026, 10, 12))
    before = _forecast(
        db, "sprint", "Sprint 1", date(2026, 10, 6), date(2026, 10, 8), date(2026, 10, 9)
    )
    after = _forecast(db, "sprint", "Sprint 1", date(2026, 10, 7), date(2026, 10, 12), None, hour=8)
    result = moved(db, "sprint", "Sprint 1")
    assert result["latest"]["id"] == after.id
    assert result["previous"]["id"] == before.id
    assert result["p50_moved_days"] == 4
    assert result["p85_moved_days"] is None  # no P85 to compare against


def test_serialize_gives_iso_dates_and_the_full_seed(db):
    row = _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), date(2026, 10, 9), None)
    data = serialize(row)
    assert data["as_of"] == "2026-10-05"
    assert data["created_at"] == "2026-10-05T09:00:00"
    assert data["p50"] == "2026-10-09" and data["p85"] is None
    assert data["seed"] == 2**32 - 1
    json.dumps(data)


def test_the_dashboard_shows_the_newest_sprint_and_every_epic_with_trails(db):
    first = date(2026, 9, 1)
    for offset in range(TRAIL + 5):
        day = first + timedelta(days=offset)
        _forecast(db, "sprint", "Sprint 12", day, day + timedelta(days=3), day + timedelta(days=5))
    newest = date(2026, 10, 9)
    _forecast(db, "sprint", "Sprint 13", newest, newest, newest)
    _forecast(db, "epic", "Pipeline", date(2026, 10, 5), date(2026, 11, 1), date(2026, 11, 9))
    _forecast(db, "epic", "Pipeline", date(2026, 10, 6), date(2026, 11, 3), date(2026, 11, 9))
    _forecast(db, "epic", "Leads", date(2026, 10, 6), date(2026, 11, 3), None)

    board = dashboard(db)
    assert board["sprint"]["latest"]["subject"] == "Sprint 13"
    assert board["sprint"]["previous"] is None
    assert board["sprint"]["trail"] == [
        {
            "created_at": "2026-10-09T09:00:00",
            "as_of": "2026-10-09",
            "p50": "2026-10-09",
            "p85": "2026-10-09",
        }
    ]
    assert [epic["latest"]["subject"] for epic in board["epics"]] == ["Leads", "Pipeline"]
    pipeline = board["epics"][1]
    assert pipeline["p50_moved_days"] == 2 and pipeline["p85_moved_days"] == 0
    assert [point["as_of"] for point in pipeline["trail"]] == ["2026-10-05", "2026-10-06"]


def test_the_trail_keeps_the_newest_forecasts_oldest_first(db):
    first = date(2026, 9, 1)
    for offset in range(TRAIL + 5):
        day = first + timedelta(days=offset)
        _forecast(db, "sprint", "Sprint 12", day, day + timedelta(days=3), None)
    trail = dashboard(db)["sprint"]["trail"]
    days = [point["as_of"] for point in trail]
    assert len(trail) == TRAIL
    assert days == sorted(days)
    assert days[0] == (first + timedelta(days=5)).isoformat()
    assert days[-1] == (first + timedelta(days=TRAIL + 4)).isoformat()


def test_an_empty_dashboard(db):
    assert dashboard(db) == {"sprint": None, "epics": []}


def test_the_dashboard_runs_no_simulation(db, monkeypatch):
    _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), date(2026, 10, 9), None)

    def refuse(*args, **kwargs):
        raise AssertionError("the dashboard never simulates")

    monkeypatch.setattr(forecaster, "sprint_forecast", refuse)
    monkeypatch.setattr("sdlc.forecast.simulate", refuse)
    assert dashboard(db)["sprint"]["latest"]["subject"] == "Sprint 1"


# Reset


def _audit(db, row, agent=AGENT):
    record_decision(
        db,
        agent=agent,
        agent_version="v1",
        subject_type=row.kind,
        subject_source=row.source,
        subject_id=row.id,
        trigger="schedule",
        head_sha=row.inputs_hash[:40],
        action_taken={"saved_forecast": row.id},
    )


def test_reset_forgets_only_simulated_forecasts_and_their_audit_rows(db):
    simulated = _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), None, None)
    real = _forecast(db, "epic", "Pipeline", date(2026, 10, 5), None, None, source="github")
    mixed = _forecast(db, "epic", "Leads", date(2026, 10, 5), None, None, source="mixed")
    for row in (simulated, real, mixed):
        _audit(db, row)
    _audit(db, simulated, agent="planner")  # another agent's row about simulated work
    db.commit()

    forecaster.reset(db)
    db.commit()

    assert set(db.scalars(select(Forecast.source))) == {"github", "mixed"}
    kept = db.execute(select(AgentDecision.agent, AgentDecision.subject_source)).all()
    assert sorted(kept) == [
        ("forecaster", "github"),
        ("forecaster", "mixed"),
        ("planner", "synthetic"),
    ]


# MySQL

MYSQL_NOW = datetime(2031, 3, 12, 10, 0)  # a Wednesday, well clear of any generated history
MYSQL_SPRINT = "MySQL check sprint"


def _small_history(db):
    """Closed, estimated issues over the past weeks and an open sprint with three items."""
    sprint = Sprint(
        name=MYSQL_SPRINT,
        start_date=date(2031, 3, 3),
        end_date=date(2031, 3, 16),
        source="synthetic",
    )
    db.add(sprint)
    db.flush()
    for k in range(12):
        closed = MYSQL_NOW - timedelta(days=2 + k * 3)
        db.add(
            Issue(
                number=9000 + k,
                external_id=f"mysql-check-{k}",
                title=f"Closed {k}",
                module="leads" if k % 2 else "forecasting",
                estimate_points=2 + k % 3,
                actual_days=3.0 + k % 4,
                state="closed",
                created_at=closed - timedelta(days=10),
                closed_at=closed,
                source="synthetic",
            )
        )
    for k in range(3):
        db.add(
            Issue(
                number=9100 + k,
                external_id=f"mysql-check-open-{k}",
                title=f"Open {k}",
                module="leads",
                estimate_points=3,
                created_at=MYSQL_NOW - timedelta(days=5),
                sprint_id=sprint.id,
                source="synthetic",
            )
        )
    db.commit()


def _forget_small_history(db):
    ids = list(db.scalars(select(Forecast.id).where(Forecast.subject == MYSQL_SPRINT)))
    if ids:
        db.execute(
            delete(AgentDecision).where(
                AgentDecision.agent == AGENT, AgentDecision.subject_id.in_(ids)
            )
        )
        db.execute(delete(Forecast).where(Forecast.id.in_(ids)))
    db.execute(delete(Issue).where(Issue.external_id.like("mysql-check-%")))
    db.execute(delete(Sprint).where(Sprint.name == MYSQL_SPRINT))
    db.commit()


def test_the_forecaster_saves_a_forecast_on_mysql():
    """Runs only when ``DATABASE_URL`` is set to MySQL, as in CI's MySQL job."""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("mysql"):
        pytest.skip("DATABASE_URL is not MySQL")
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
        mysql = create_engine(url)
        try:
            with Session(mysql) as db:
                _forget_small_history(db)
                _small_history(db)
                try:
                    summary = ForecastRunner("shadow", engine=mysql, runs=RUNS).poll_once(MYSQL_NOW)
                    assert summary == {"mode": "shadow", "assessed": 1, "unchanged": 0}
                    row = db.scalars(select(Forecast).where(Forecast.subject == MYSQL_SPRINT)).one()
                    assert row.trigger == "schedule"
                    assert row.remaining_items == 3 and row.remaining_points == 9
                    assert row.p50 is not None and row.history_days > 0
                    audit = db.scalars(
                        select(AgentDecision).where(
                            AgentDecision.agent == AGENT, AgentDecision.subject_id == row.id
                        )
                    ).one()
                    assert audit.action_taken == {"saved_forecast": row.id}
                finally:
                    db.rollback()
                    _forget_small_history(db)
        finally:
            mysql.dispose()
    finally:
        get_settings.cache_clear()
