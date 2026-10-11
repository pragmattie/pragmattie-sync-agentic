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
    REAL_NOTE,
    SIMULATED_NOTE,
    TRAIL,
    ForecastRunner,
    _fingerprint,
    dashboard,
    epic_inputs,
    latest,
    moved,
    serialize,
    sprint_inputs,
)
from sdlc.tables import AgentDecision, Epic, Forecast, Issue, PullRequest, Sprint
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


def _counts(engine, kind="sprint"):
    with Session(engine) as db:
        return (
            db.scalar(select(func.count(Forecast.id)).where(Forecast.kind == kind)),
            db.scalar(
                select(func.count(AgentDecision.id)).where(
                    AgentDecision.agent == AGENT, AgentDecision.subject_type == kind
                )
            ),
        )


def _triggers(engine, kind="sprint"):
    with Session(engine) as db:
        query = select(Forecast.trigger).where(Forecast.kind == kind)
        return list(db.scalars(query.order_by(Forecast.id)))


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
    # The sprint and the four simulated epics.
    assert runner.poll_once(WEDNESDAY) == {"mode": "shadow", "assessed": 5, "unchanged": 0}
    assert _counts(engine) == (1, 1)
    assert _triggers(engine) == ["schedule"]

    later = WEDNESDAY + timedelta(hours=3)
    assert runner.poll_once(later) == {"mode": "shadow", "assessed": 0, "unchanged": 5}
    assert _counts(engine) == (1, 1)

    with Session(engine) as db:
        item = next(item for item in _open_items(db) if item.epic is None)
        item.state = "closed"
        item.closed_at = later
        db.commit()
    assert runner.poll_once(later) == {"mode": "shadow", "assessed": 1, "unchanged": 4}
    assert _counts(engine) == (2, 2)
    assert _triggers(engine) == ["schedule", "change"]

    tomorrow = WEDNESDAY + timedelta(days=1)
    assert runner.poll_once(tomorrow)["assessed"] == 5
    assert _counts(engine) == (3, 3)
    assert runner.poll_once(tomorrow)["unchanged"] == 5

    assert runner.poll_once(tomorrow, force=True)["assessed"] == 5
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
    assert runner.poll_once(WEDNESDAY + timedelta(minutes=5))["assessed"] >= 1
    assert _triggers(engine) == ["schedule", "change"]


def test_the_saved_row_and_its_audit_row(engine, runner):
    runner.poll_once(WEDNESDAY)
    with Session(engine) as db:
        row = latest(db, "sprint", "Sprint 13")
        decision = db.scalars(
            select(AgentDecision).where(AgentDecision.subject_type == "sprint")
        ).one()

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
    assert _counts(engine, "epic") == (0, 0)


def test_no_sprint_in_progress_saves_no_sprint_forecast(engine, runner):
    weekend_after = datetime(2027, 1, 1, 9)  # long after the last simulated sprint
    assert runner.poll_once(weekend_after) == {"mode": "shadow", "assessed": 4, "unchanged": 0}
    assert _counts(engine) == (0, 0)
    assert _counts(engine, "epic") == (4, 4)


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
    assert [line["assessed"] for line in printed] == [5, 0, 5]
    assert _triggers(engine) == ["schedule", "manual"]


# Epics

MILESTONE = "M5 Delivery forecasting"


@pytest.fixture
def milestone(engine):
    """A real milestone due Oct 16 with two open stories, and real closures since Sep 28."""
    with Session(engine) as db:
        db.add(
            Epic(
                source="github",
                external_id="milestone-5",
                number=5,
                name=MILESTONE,
                due_on=date(2026, 10, 16),
                created_at=datetime(2026, 9, 28, 9),
            )
        )
        for k in range(6):
            db.add(
                Issue(
                    source="github",
                    external_id=f"real-closed-{k}",
                    number=100 + k,
                    title=f"Real {k}",
                    created_at=datetime(2026, 9, 28, 9),
                    state="closed",
                    closed_at=datetime(2026, 9, 28, 12) + timedelta(days=k),
                )
            )
        for k, points in enumerate((3, None)):
            db.add(
                Issue(
                    source="github",
                    external_id=f"real-open-{k}",
                    number=200 + k,
                    title=f"Story {k}",
                    estimate_points=points,
                    epic=MILESTONE,
                    created_at=datetime(2026, 9, 28, 9),
                )
            )
        db.commit()
    return MILESTONE


def _epic_rows(engine, subject):
    with Session(engine) as db:
        query = select(Forecast).where(Forecast.kind == "epic", Forecast.subject == subject)
        rows = list(db.scalars(query.order_by(Forecast.id)))
        db.expunge_all()
        return rows


def test_epic_inputs_list_open_stories_and_the_due_date(engine, milestone):
    with Session(engine) as db:
        rows = epic_inputs(db, MILESTONE)
        ids = [issue.id for issue in db.scalars(select(Issue).where(Issue.epic == MILESTONE))]
    assert sorted(rows[:-1], key=lambda row: row["id"]) == [
        {"id": ids[0], "points": 3, "source": "github"},
        {"id": ids[1], "points": None, "source": "github"},
    ]
    assert rows[-1] == {"due_on": "2026-10-16"}


def test_epic_rows_are_saved_with_their_source_target_and_probability(engine, runner, milestone):
    assert runner.poll_once(WEDNESDAY) == {"mode": "shadow", "assessed": 6, "unchanged": 0}
    assert _counts(engine, "epic") == (5, 5)

    (real,) = _epic_rows(engine, MILESTONE)
    assert (real.source, real.trigger) == ("github", "schedule")
    assert (real.remaining_items, real.remaining_real, real.remaining_points) == (2, 2, 3)
    assert real.end_date == date(2026, 10, 16)
    assert 0.0 <= real.on_time_probability <= 1.0
    assert real.history_days == 7  # Sep 28 to Oct 6
    assert real.at_risk == []

    with Session(engine) as db:
        simulated = list(
            db.scalars(
                select(Forecast).where(Forecast.kind == "epic", Forecast.subject != MILESTONE)
            )
        )
        assert {row.source for row in simulated} == {"synthetic"}
        assert all(row.end_date is None and row.on_time_probability is None for row in simulated)
        assert all(row.remaining_real == 0 for row in simulated)

        decision = db.scalars(
            select(AgentDecision).where(
                AgentDecision.subject_type == "epic", AgentDecision.subject_id == real.id
            )
        ).one()
        assert decision.subject_source == "github"
        assert decision.output["end_date"] == "2026-10-16"
        assert decision.output["rationale"].startswith("Delivery forecasting (M5): 2 items left")
        assert "agents' pace" in decision.output["rationale"]

    assert runner.poll_once(WEDNESDAY + timedelta(hours=1))["assessed"] == 0
    assert _counts(engine, "epic") == (5, 5)


@pytest.mark.parametrize("change", ["due date", "add", "re-estimate", "close"])
def test_a_change_to_a_real_epic_re_forecasts_it(engine, runner, milestone, change):
    runner.poll_once(WEDNESDAY)
    with Session(engine) as db:
        story = db.scalars(select(Issue).where(Issue.epic == MILESTONE).order_by(Issue.id)).first()
        if change == "due date":
            epic = db.scalars(select(Epic).where(Epic.name == MILESTONE)).one()
            epic.due_on = date(2026, 10, 9)
        elif change == "add":
            db.add(
                Issue(
                    source="github",
                    external_id="real-added",
                    title="Added",
                    epic=MILESTONE,
                    created_at=WEDNESDAY,
                )
            )
        elif change == "re-estimate":
            story.estimate_points = 5
        else:
            story.state = "closed"
            story.closed_at = WEDNESDAY
        db.commit()
    assert runner.poll_once(WEDNESDAY + timedelta(minutes=5)) == {
        "mode": "shadow",
        "assessed": 1,
        "unchanged": 5,
    }
    rows = _epic_rows(engine, MILESTONE)
    assert [row.trigger for row in rows] == ["schedule", "change"]
    if change == "due date":
        assert rows[1].end_date == date(2026, 10, 9)
        assert rows[1].on_time_probability <= rows[0].on_time_probability
    if change == "add":
        assert rows[1].remaining_items == 3
        assert rows[1].p50 >= rows[0].p50 and rows[1].p85 >= rows[0].p85


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


def test_moved_without_a_previous_forecast_is_all_none(db):
    only = _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), date(2026, 10, 9), None)
    assert moved(only, None) == {
        "p50_days": None,
        "p85_days": None,
        "previous_p50": None,
        "previous_p85": None,
        "previous_at": None,
    }


def test_moved_counts_the_days_p50_and_p85_moved(db):
    before = _forecast(
        db, "sprint", "Sprint 1", date(2026, 10, 6), date(2026, 10, 8), date(2026, 10, 9)
    )
    after = _forecast(db, "sprint", "Sprint 1", date(2026, 10, 7), date(2026, 10, 12), None, hour=8)
    assert moved(after, before) == {
        "p50_days": 4,
        "p85_days": None,  # no P85 to compare against
        "previous_p50": "2026-10-08",
        "previous_p85": "2026-10-09",
        "previous_at": "2026-10-06T09:00:00",
    }


def test_serialize_gives_iso_dates_and_the_full_seed(db):
    row = _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), date(2026, 10, 9), None)
    data = serialize(row)
    assert data["as_of"] == "2026-10-05"
    assert data["created_at"] == "2026-10-05T09:00:00"
    assert data["p50"] == "2026-10-09" and data["p85"] is None
    assert data["seed"] == 2**32 - 1
    json.dumps(data)


def _open_milestone(db, name, state="open"):
    db.add(
        Epic(
            source="github",
            external_id=f"milestone-{name.split()[0]}",
            name=name,
            state=state,
            created_at=datetime(2026, 9, 1),
        )
    )
    db.flush()


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
    assert board["sprint"]["subject"] == "Sprint 13"
    assert board["sprint"]["kind_note"] == SIMULATED_NOTE
    assert board["sprint"]["moved"]["previous_at"] is None
    assert board["sprint"]["trail"] == [
        {"at": "2026-10-09T09:00:00", "p50": "2026-10-09", "p85": "2026-10-09"}
    ]
    assert "inputs_hash" not in board["sprint"] and "seed" not in board["sprint"]
    assert board["sprint"]["at_risk"] == []
    assert [epic["subject"] for epic in board["epics"]] == ["Leads", "Pipeline"]
    pipeline = board["epics"][1]
    assert pipeline["moved"]["p50_days"] == 2 and pipeline["moved"]["p85_days"] == 0
    assert [point["at"] for point in pipeline["trail"]] == [
        "2026-10-05T09:00:00",
        "2026-10-06T09:00:00",
    ]
    assert board["proposals"] == {}
    assert board["saved"] == TRAIL + 5 + 4
    assert board["as_of"] == "2026-10-09"


def test_real_epics_come_first_in_milestone_order_then_simulated_by_name(db):
    day = date(2026, 10, 6)
    for name in ("M10 Launch", "M6 Accuracy", "M9 Planner", "M5 Forecasting"):
        _open_milestone(db, name)
    _forecast(db, "epic", "Pipeline", day, day, day)
    _forecast(db, "epic", "M10 Launch", day, day, day, source="github")
    _forecast(db, "epic", "Leads", day, day, day)
    _forecast(db, "epic", "M6 Accuracy", day, day, day, source="mixed")
    _forecast(db, "epic", "M9 Planner", day, day, day, source="github")
    _forecast(db, "epic", "M5 Forecasting", day, day, day, source="github")

    epics = dashboard(db)["epics"]
    assert [(epic["label"], epic["source"], epic["kind_note"]) for epic in epics] == [
        ("Forecasting (M5)", "github", REAL_NOTE),
        ("Accuracy (M6)", "mixed", REAL_NOTE),
        ("Planner (M9)", "github", REAL_NOTE),
        ("Launch (M10)", "github", REAL_NOTE),
        ("Leads", "synthetic", SIMULATED_NOTE),
        ("Pipeline", "synthetic", SIMULATED_NOTE),
    ]


def test_a_closed_milestone_leaves_the_dashboard_but_its_forecasts_stay(db):
    day = date(2026, 10, 6)
    _open_milestone(db, "M5 Forecasting", state="closed")
    _open_milestone(db, "M6 Accuracy")
    _forecast(db, "epic", "M5 Forecasting", day, day, day, source="github")
    _forecast(db, "epic", "M6 Accuracy", day, day, day, source="github")

    board = dashboard(db)
    assert [epic["subject"] for epic in board["epics"]] == ["M6 Accuracy"]
    assert board["saved"] == 2
    assert db.scalar(select(func.count()).where(Forecast.subject == "M5 Forecasting")) == 1


def test_the_trail_keeps_the_newest_forecasts_oldest_first(db):
    first = date(2026, 9, 1)
    for offset in range(TRAIL + 5):
        day = first + timedelta(days=offset)
        _forecast(db, "sprint", "Sprint 12", day, day + timedelta(days=3), None)
    trail = dashboard(db)["sprint"]["trail"]
    times = [point["at"] for point in trail]
    assert len(trail) == TRAIL
    assert times == sorted(times)
    assert times[0].startswith((first + timedelta(days=5)).isoformat())
    assert times[-1].startswith((first + timedelta(days=TRAIL + 4)).isoformat())


def test_an_empty_dashboard(db):
    assert dashboard(db) == {
        "epics": [],
        "sprint": None,
        "proposals": {},
        "saved": 0,
        "as_of": None,
    }


def test_the_dashboard_runs_no_simulation(db, monkeypatch):
    _forecast(db, "sprint", "Sprint 1", date(2026, 10, 5), date(2026, 10, 9), None)

    def refuse(*args, **kwargs):
        raise AssertionError("the dashboard never simulates")

    monkeypatch.setattr(forecaster, "sprint_forecast", refuse)
    monkeypatch.setattr("sdlc.forecast.simulate", refuse)
    assert dashboard(db)["sprint"]["subject"] == "Sprint 1"


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
