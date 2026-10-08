from datetime import date, datetime

import pytest
from sqlalchemy.orm import Session

from sdlc.db import Base
from sdlc.flow import STAGES, available, flow
from sdlc.main import app
from sdlc.routers.signals import get_now
from sdlc.tables import AgentDecision, Deployment, Issue, PullRequest

TODAY = date(2026, 10, 2)  # a Friday


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _issue(db, number, created, *, source="github", module=None, points=None, **fields):
    issue = Issue(
        source=source,
        number=number,
        title=f"Issue {number}",
        module=module,
        estimate_points=points,
        created_at=created,
        **fields,
    )
    db.add(issue)
    db.flush()
    return issue


def _pr(db, issue, number, opened, *, source="github", **fields):
    pr = PullRequest(
        source=source, number=number, title=f"PR {number}", issue_id=issue.id, created_at=opened
    )
    for key, value in fields.items():
        setattr(pr, key, value)
    db.add(pr)
    db.flush()
    return pr


def _decision(db, agent, subject_type, number, at, *, source="github", status="ok"):
    db.add(
        AgentDecision(
            created_at=at,
            agent=agent,
            agent_version="1",
            subject_type=subject_type,
            subject_source=source,
            subject_id=number,
            trigger="test",
            status=status,
            attempt=at.day,
        )
    )
    db.flush()


def _day(result, day):
    return next(row["counts"] for row in result["series"] if row["date"] == day)


def _stage_on(result, day):
    counts = _day(result, day)
    return [stage for stage in STAGES if counts[stage]]


def test_real_issue_moves_through_every_stage(db):
    issue = _issue(db, 1, datetime(2026, 9, 20, 9))
    _decision(db, "triage", "issue", 1, datetime(2026, 9, 21, 10))
    _decision(db, "triage", "issue", 1, datetime(2026, 9, 21, 9), status="error")
    _pr(db, issue, 10, datetime(2026, 9, 23, 9), merged_at=datetime(2026, 9, 25, 15))
    _decision(db, "pr_risk", "pr", 10, datetime(2026, 9, 24, 9))
    db.add(Deployment(source="synthetic", version="s", deployed_at=datetime(2026, 9, 26)))
    db.add(Deployment(source="github", version="g0", deployed_at=datetime(2026, 9, 25, 12)))
    db.add(Deployment(source="github", version="g1", deployed_at=datetime(2026, 9, 27, 8)))
    db.flush()

    result = flow(db, source="github", days=14, today=TODAY)

    assert _stage_on(result, date(2026, 9, 19)) == []
    assert _stage_on(result, date(2026, 9, 20)) == ["Backlog"]
    assert _stage_on(result, date(2026, 9, 21)) == ["Triaged"]
    assert _stage_on(result, date(2026, 9, 23)) == ["In progress"]
    assert _stage_on(result, date(2026, 9, 24)) == ["In review"]
    assert _stage_on(result, date(2026, 9, 25)) == ["Merged"]
    assert _stage_on(result, date(2026, 9, 26)) == ["Merged"]  # synthetic deploys don't count
    assert _stage_on(result, date(2026, 9, 27)) == ["Production"]
    assert result["items"] == 1


def test_real_issue_with_module_and_points_is_triaged_at_creation(db):
    _issue(db, 1, datetime(2026, 9, 28, 9), module="leads", points=3)
    _issue(db, 2, datetime(2026, 9, 28, 9), module="leads")

    result = flow(db, source="github", days=7, today=TODAY)

    assert _day(result, date(2026, 9, 28))["Triaged"] == 1
    assert _day(result, date(2026, 9, 28))["Backlog"] == 1


def test_synthetic_issue_is_triaged_at_creation(db):
    _issue(db, 1, datetime(2026, 9, 28, 9), source="synthetic")

    result = flow(db, source="synthetic", days=7, today=TODAY)

    assert _stage_on(result, date(2026, 9, 28)) == ["Triaged"]
    assert flow(db, source="github", days=7, today=TODAY)["items"] == 0


def test_issue_closed_unmerged_leaves_the_flow(db):
    issue = _issue(
        db, 1, datetime(2026, 9, 26), state="closed", closed_at=datetime(2026, 9, 29, 10)
    )
    _pr(db, issue, 10, datetime(2026, 9, 27), state="closed", closed_at=datetime(2026, 9, 28))

    result = flow(db, source="github", days=7, today=TODAY)

    assert _stage_on(result, date(2026, 9, 27)) == ["In progress"]
    assert _stage_on(result, date(2026, 9, 28)) == ["Backlog"]  # its PR closed unmerged
    assert _stage_on(result, date(2026, 9, 29)) == []
    assert _stage_on(result, TODAY) == []


def test_most_advanced_pr_decides(db):
    issue = _issue(db, 1, datetime(2026, 9, 26))
    _pr(db, issue, 10, datetime(2026, 9, 27))
    _pr(db, issue, 11, datetime(2026, 9, 28))
    _decision(db, "pr_risk", "pr", 11, datetime(2026, 9, 29))

    result = flow(db, source="github", days=7, today=TODAY)

    assert _stage_on(result, date(2026, 9, 28)) == ["In progress"]
    assert _stage_on(result, date(2026, 9, 29)) == ["In review"]


def test_counts_are_taken_at_the_end_of_each_day(db):
    _issue(db, 1, datetime(2026, 9, 29, 23, 59, 59))
    _issue(db, 2, datetime(2026, 9, 30, 0, 0))

    result = flow(db, source="github", days=7, today=TODAY)

    assert _day(result, date(2026, 9, 28))["Backlog"] == 0
    assert _day(result, date(2026, 9, 29))["Backlog"] == 1
    assert _day(result, date(2026, 9, 30))["Backlog"] == 2
    assert [row["date"] for row in result["series"]][0] == date(2026, 9, 26)
    assert result["series"][-1]["date"] == TODAY


def test_throughput_weeks_start_on_monday(db):
    for number, merged in enumerate(
        [datetime(2026, 9, 20, 23), datetime(2026, 9, 21, 0), datetime(2026, 9, 27, 23)], 1
    ):
        issue = _issue(db, number, datetime(2026, 9, 1))
        _pr(db, issue, 10 + number, datetime(2026, 9, 2), merged_at=merged)

    result = flow(db, source="github", days=14, today=TODAY)

    assert result["throughput"] == [
        {"week": date(2026, 9, 14), "merged": 1},
        {"week": date(2026, 9, 21), "merged": 2},
        {"week": date(2026, 9, 28), "merged": 0},
    ]


def test_cycle_time_median_and_p85(db):
    for number, days in enumerate([1, 2, 3, 4, 10], 1):
        issue = _issue(db, number, datetime(2026, 9, 1))
        _pr(db, issue, 10 + number, datetime(2026, 9, 10), merged_at=datetime(2026, 9, 10 + days))
    old = _issue(db, 9, datetime(2026, 8, 1))
    _pr(db, old, 99, datetime(2026, 8, 1), merged_at=datetime(2026, 8, 20))  # before the window

    result = flow(db, source="github", days=30, today=TODAY)

    assert result["cycle_time_days"] == {"median": 3.0, "p85": 6.4}
    assert sum(week["merged"] for week in result["throughput"]) == 5


def test_empty_source_gives_zero_counts(db):
    result = flow(db, source="synthetic", days=7, today=TODAY)

    assert result["stages"] == list(STAGES)
    assert len(result["series"]) == 7
    assert all(count == 0 for row in result["series"] for count in row["counts"].values())
    assert result["cycle_time_days"] == {"median": None, "p85": None}
    assert result["items"] == 0
    assert available(db) == {"synthetic": 0, "github": 0}


@pytest.fixture
def api(client, sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        _issue(session, 1, datetime(2026, 9, 1), source="synthetic")
        _issue(session, 2, datetime(2026, 9, 1), source="synthetic")
        _issue(session, 3, datetime(2026, 9, 1))
        session.commit()
    app.dependency_overrides[get_now] = lambda: datetime(2026, 10, 2, 12)
    yield client
    app.dependency_overrides.pop(get_now, None)


def test_flow_route_defaults(api):
    body = api.get("/api/v1/signals/flow").json()

    assert body["source"] == "github"
    assert body["days"] == 60
    assert len(body["series"]) == 60
    assert body["series"][-1]["date"] == "2026-10-02"
    assert body["available"] == {"synthetic": 2, "github": 1}


def test_flow_route_source(api):
    body = api.get("/api/v1/signals/flow", params={"source": "synthetic", "days": 7}).json()

    assert body["source"] == "synthetic"
    assert body["series"][-1]["counts"]["Triaged"] == 2


@pytest.mark.parametrize("days", [6, 181])
def test_flow_route_days_bounds(api, days):
    assert api.get("/api/v1/signals/flow", params={"days": days}).status_code == 422


@pytest.mark.parametrize("days", [7, 180])
def test_flow_route_days_edges(api, days):
    assert api.get("/api/v1/signals/flow", params={"days": days}).status_code == 200


def test_flow_route_rejects_unknown_source(api):
    assert api.get("/api/v1/signals/flow", params={"source": "v1"}).status_code == 422
