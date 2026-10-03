from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sdlc.db import Base
from sdlc.main import app
from sdlc.routers.signals import get_now
from sdlc.tables import CIRun, Deployment, Incident, Issue, PullRequest, Sprint

NOW = datetime(2026, 10, 2, 12, 0)  # a Friday
SPRINT_ONE = date(2026, 9, 7)
SPRINT_TWO = date(2026, 9, 21)

ENDPOINTS = ["summary", "sprints", "cycle-time", "ci", "modules", "sources"]


@pytest.fixture
def api(client, sqlite_engine):
    """A client over a small simulated history, with "now" fixed at NOW."""
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        one = Sprint(name="Sprint 1", start_date=SPRINT_ONE, end_date=SPRINT_ONE + timedelta(13))
        two = Sprint(name="Sprint 2", start_date=SPRINT_TWO, end_date=SPRINT_TWO + timedelta(13))
        db.add_all([one, two])
        db.flush()
        db.add_all(
            [
                Issue(
                    title="Lead import",
                    module="leads",
                    sprint_id=one.id,
                    estimate_points=3,
                    actual_days=2.0,
                    state="closed",
                    created_at=datetime(2026, 9, 1),
                    closed_at=datetime(2026, 9, 15),
                    source="synthetic",
                ),
                Issue(
                    title="Forecast rollup",
                    module="forecasting",
                    sprint_id=two.id,
                    estimate_points=5,
                    state="open",
                    created_at=datetime(2026, 9, 1),
                    source="synthetic",
                ),
            ]
        )
        merged = PullRequest(
            title="Import leads",
            module="leads",
            state="merged",
            created_at=datetime(2026, 9, 14, 9),
            merged_at=datetime(2026, 9, 15, 9),
            source="synthetic",
        )
        db.add(merged)
        db.add(
            PullRequest(
                title="Fix rollup",
                module="forecasting",
                state="merged",
                created_at=datetime(2026, 9, 28, 9),
                merged_at=datetime(2026, 9, 28, 21),
                source="github",
            )
        )
        deployment = Deployment(
            version="v1.0.0", deployed_at=datetime(2026, 9, 16, 10), status="success"
        )
        db.add(deployment)
        db.flush()
        db.add(
            Incident(
                title="leads degraded",
                severity="sev2",
                module="leads",
                opened_at=datetime(2026, 9, 16, 12),
                resolved_at=datetime(2026, 9, 16, 15),
                deployment_id=deployment.id,
            )
        )
        db.add_all(
            [
                CIRun(suite="api", conclusion="success", started_at=datetime(2026, 9, 28, 10)),
                CIRun(
                    suite="api",
                    conclusion="failure",
                    flaky=True,
                    started_at=datetime(2026, 9, 29, 10),
                ),
            ]
        )
        db.commit()
    app.dependency_overrides[get_now] = lambda: NOW
    return client


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_every_endpoint_returns_json(api, endpoint):
    response = api.get(f"/api/v1/signals/{endpoint}")

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/json"


def test_summary_defaults_to_thirty_days(api):
    body = api.get("/api/v1/signals/summary").json()

    assert body["days"] == 30
    assert body["current"]["deployments"] == 1
    assert body["current"]["incidents"] == 1
    assert body["current"]["change_failure_rate"] == 100.0
    assert body["current"]["time_to_restore_hours"] == 3.0
    assert set(body["previous"]) == set(body["current"])
    assert api.get("/api/v1/signals/summary", params={"days": 7}).json()["days"] == 7


def test_sprints_report_dates_as_plain_days(api):
    rows = api.get("/api/v1/signals/sprints").json()

    assert [row["sprint"] for row in rows] == ["Sprint 1", "Sprint 2"]
    assert rows[0]["start"] == "2026-09-07"
    assert rows[0]["end"] == "2026-09-20"
    assert rows[0]["committed"] == rows[0]["completed"] == 3
    assert rows[1]["in_progress"] is True
    assert rows[1]["committed"] == 5
    assert rows[1]["completed"] == 0


def test_cycle_time_is_per_sprint_by_default(api):
    rows = api.get("/api/v1/signals/cycle-time").json()

    assert [row["sprint"] for row in rows] == ["Sprint 1", "Sprint 2"]
    assert rows[0]["median_hours"] == 24.0
    assert rows[1]["median_hours"] == 12.0


def test_cycle_time_by_week_returns_weekly_rows(api):
    rows = api.get("/api/v1/signals/cycle-time", params={"bucket": "week", "weeks": 4}).json()

    assert [row["week"] for row in rows] == [
        "2026-09-07",
        "2026-09-14",
        "2026-09-21",
        "2026-09-28",
    ]
    assert [row["merged"] for row in rows] == [0, 1, 0, 1]
    assert len(api.get("/api/v1/signals/cycle-time", params={"bucket": "week"}).json()) == 26


def test_ci_defaults_to_twenty_six_weeks(api):
    body = api.get("/api/v1/signals/ci").json()

    assert len(body["by_week"]) == 26
    assert body["by_week"][-1] == {"week": "2026-09-28", "runs": 2, "pass_rate": 50.0}
    assert body["by_suite"] == [{"suite": "api", "runs": 2, "pass_rate": 50.0, "flaky_rate": 50.0}]
    assert len(api.get("/api/v1/signals/ci", params={"weeks": 1}).json()["by_week"]) == 1


def test_modules_and_sources(api):
    modules = api.get("/api/v1/signals/modules").json()
    assert [row["module"] for row in modules] == ["forecasting", "leads"]
    assert modules[1]["incidents"] == 1
    assert modules[1]["days_per_point"] == 0.67

    counts = api.get("/api/v1/signals/sources").json()
    assert counts["pull_requests"] == {"synthetic": 1, "github": 1}
    assert set(counts) == {"issues", "pull_requests", "ci_runs", "deployments", "incidents"}


@pytest.mark.parametrize(
    ("endpoint", "params"),
    [
        ("summary", {"days": 3}),
        ("summary", {"days": 181}),
        ("cycle-time", {"bucket": "month"}),
        ("cycle-time", {"bucket": "week", "weeks": 0}),
        ("ci", {"weeks": 0}),
        ("ci", {"weeks": 105}),
    ],
)
def test_out_of_range_queries_are_rejected(api, endpoint, params):
    assert api.get(f"/api/v1/signals/{endpoint}", params=params).status_code == 422


@pytest.mark.parametrize("endpoint", ENDPOINTS)
def test_signals_are_read_only(api, endpoint):
    assert api.post(f"/api/v1/signals/{endpoint}").status_code == 405


def test_cors_allows_get_from_a_configured_origin(api):
    response = api.options(
        "/api/v1/signals/summary",
        headers={"Origin": "http://localhost:5174", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5174"
    assert "GET" in response.headers["access-control-allow-methods"]
    assert "POST" not in response.headers["access-control-allow-methods"]


def test_cors_rejects_a_preflight_for_post(api):
    response = api.options(
        "/api/v1/signals/summary",
        headers={"Origin": "http://localhost:5174", "Access-Control-Request-Method": "POST"},
    )

    assert response.status_code == 400


def test_cors_ignores_an_unknown_origin(api):
    response = api.options(
        "/api/v1/signals/summary",
        headers={"Origin": "http://evil.example", "Access-Control-Request-Method": "GET"},
    )

    assert "access-control-allow-origin" not in response.headers


def test_openapi_lists_the_six_endpoints_with_their_queries(api):
    paths = api.get("/openapi.json").json()["paths"]

    def queries(endpoint):
        operation = paths[f"/api/v1/signals/{endpoint}"]["get"]
        return {p["name"] for p in operation.get("parameters", []) if p["in"] == "query"}

    assert {path for path in paths if path.startswith("/api/v1/signals/")} == {
        f"/api/v1/signals/{endpoint}" for endpoint in ENDPOINTS
    }
    assert queries("summary") == {"days"}
    assert queries("cycle-time") == {"bucket", "weeks"}
    assert queries("ci") == {"weeks"}
    assert queries("sprints") == queries("modules") == queries("sources") == set()
    assert "/api/v1/health" in paths
