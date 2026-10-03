from datetime import date, datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sdlc import metrics, synth
from sdlc.db import Base
from sdlc.tables import CIRun, Deployment, Incident, Issue, PullRequest, Sprint

NOW = datetime(2026, 10, 2, 12, 0)  # a Friday
EPOCH = datetime(2026, 1, 1)


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _deploy(db, at, status="success", **fields):
    deployment = Deployment(version="v", deployed_at=at, status=status, **fields)
    db.add(deployment)
    db.flush()
    return deployment


def _incident(db, opened, resolved=None, **fields):
    incident = Incident(
        title="i", severity="sev2", opened_at=opened, resolved_at=resolved, **fields
    )
    db.add(incident)
    db.flush()
    return incident


def _pr(db, opened, merged=None, **fields):
    pr = PullRequest(
        title="pr",
        state="merged" if merged else "open",
        created_at=opened,
        merged_at=merged,
        **fields,
    )
    db.add(pr)
    db.flush()
    return pr


def _ci(db, started, suite="api", conclusion="success", flaky=False, **fields):
    db.add(CIRun(suite=suite, conclusion=conclusion, flaky=flaky, started_at=started, **fields))
    db.flush()


def _issue(db, points=None, closed=None, days=None, **fields):
    db.add(
        Issue(
            title="issue",
            estimate_points=points,
            actual_days=days,
            state="closed" if closed else "open",
            created_at=EPOCH,
            closed_at=closed,
            **fields,
        )
    )
    db.flush()


def _sprint(db, name, start, goal=None):
    sprint = Sprint(name=name, start_date=start, end_date=start + timedelta(days=13), goal=goal)
    db.add(sprint)
    db.flush()
    return sprint


def test_percentile_interpolates_between_the_closest_ranks():
    assert metrics._percentile([], 0.5) is None
    assert metrics._percentile([7], 0.85) == 7
    assert metrics._percentile([4, 1, 3, 2], 0.5) == 2.5
    assert metrics._percentile([10, 20, 30, 40, 50], 0.85) == pytest.approx(44)
    assert metrics._percentile([1, 2, 3], 1.0) == 3
    assert metrics._percentile([1, 2, 3], 0.0) == 1


def test_an_empty_database_gives_none_measures(session):
    summary = metrics.dora_summary(session, NOW)
    empty = {
        "deploys_per_week": None,
        "lead_time_hours": None,
        "change_failure_rate": None,
        "time_to_restore_hours": None,
        "deployments": 0,
        "incidents": 0,
    }
    assert summary == {"days": 30, "current": empty, "previous": empty}
    assert metrics.sprint_velocity(session, NOW.date()) == []
    assert metrics.pr_cycle_time_by_sprint(session, NOW) == []
    weeks = metrics.pr_cycle_time(session, 2, NOW)
    assert weeks == [
        {"week": date(2026, 9, 21), "median_hours": None, "p85_hours": None, "merged": 0},
        {"week": date(2026, 9, 28), "median_hours": None, "p85_hours": None, "merged": 0},
    ]
    assert metrics.ci_health(session, 1, NOW) == {
        "by_suite": [],
        "by_week": [{"week": date(2026, 9, 28), "runs": 0, "pass_rate": None}],
    }
    assert metrics.quality_by_module(session) == []
    assert metrics.sources(session) == {
        name: {"synthetic": 0, "github": 0}
        for name in ("issues", "pull_requests", "ci_runs", "deployments", "incidents")
    }


def test_dora_window_edges(session):
    start = NOW - timedelta(days=28)
    _deploy(session, start)  # exactly at now - days: current
    _deploy(session, NOW - timedelta(hours=1))
    _deploy(session, NOW)  # at now: in neither window
    _deploy(session, start - timedelta(seconds=1))  # previous
    _deploy(session, start - timedelta(days=28))  # first moment of previous
    _deploy(session, start - timedelta(days=28, seconds=1))  # before both

    summary = metrics.dora_summary(session, NOW, days=28)
    assert summary["days"] == 28
    assert summary["current"]["deployments"] == 2
    assert summary["current"]["deploys_per_week"] == 0.5
    assert summary["previous"]["deployments"] == 2


def test_dora_lead_time_is_the_median_of_prs_merged_in_the_window(session):
    _pr(session, NOW - timedelta(days=2), NOW - timedelta(days=2) + timedelta(hours=10))
    _pr(session, NOW - timedelta(days=3), NOW - timedelta(days=3) + timedelta(hours=20))
    _pr(session, NOW - timedelta(days=4), NOW - timedelta(days=4) + timedelta(hours=35))
    _pr(session, NOW - timedelta(days=5))  # never merged
    _pr(session, NOW - timedelta(days=50), NOW - timedelta(days=40, minutes=30))  # previous

    summary = metrics.dora_summary(session, NOW)
    assert summary["current"]["lead_time_hours"] == 20.0
    assert summary["previous"]["lead_time_hours"] == 239.5


def test_a_failed_deployment_counts_once_even_with_two_incidents(session):
    day = NOW - timedelta(days=5)
    broken = _deploy(session, day)
    _deploy(session, day + timedelta(hours=1), status="rolled_back")
    _deploy(session, day + timedelta(hours=2))
    _deploy(session, day + timedelta(hours=3))
    old = _deploy(session, NOW - timedelta(days=40))
    _incident(session, day + timedelta(hours=1), deployment_id=broken.id)
    _incident(session, day + timedelta(hours=2), deployment_id=broken.id)
    # An incident opened in the current window does not make a previous deployment fail now.
    _incident(session, day, deployment_id=old.id)

    summary = metrics.dora_summary(session, NOW)
    current = summary["current"]
    assert current["deployments"] == 4
    assert current["incidents"] == 3
    assert current["change_failure_rate"] == 50.0
    assert summary["previous"]["change_failure_rate"] == 0.0


def test_time_to_restore_leaves_out_unresolved_incidents(session):
    opened = NOW - timedelta(days=3)
    _incident(session, opened, opened + timedelta(hours=1))
    _incident(session, opened, opened + timedelta(hours=2, minutes=30))
    _incident(session, opened, opened + timedelta(hours=6))
    _incident(session, opened)  # still open

    current = metrics.dora_summary(session, NOW)["current"]
    assert current["incidents"] == 4
    assert current["time_to_restore_hours"] == 2.5


def test_sprint_velocity(session):
    first = _sprint(session, "Sprint 1", date(2026, 9, 14), goal="Release 2026.3")
    second = _sprint(session, "Sprint 2", date(2026, 9, 28))
    end_of_first = datetime(2026, 9, 27, 23, 59)
    _issue(session, 3, end_of_first, sprint_id=first.id)  # last minute of the sprint
    _issue(session, 5, datetime(2026, 9, 28), sprint_id=first.id)  # the day after: carried
    _issue(session, 2, sprint_id=first.id)
    _issue(session, None, end_of_first, sprint_id=first.id)
    _issue(session, 8, datetime(2026, 9, 29), sprint_id=second.id)
    _issue(session, 13)  # not in a sprint

    rows = metrics.sprint_velocity(session, date(2026, 10, 2))
    assert rows == [
        {
            "sprint": "Sprint 1",
            "start": date(2026, 9, 14),
            "end": date(2026, 9, 27),
            "goal": "Release 2026.3",
            "in_progress": False,
            "committed": 10,
            "completed": 3,
        },
        {
            "sprint": "Sprint 2",
            "start": date(2026, 9, 28),
            "end": date(2026, 10, 11),
            "goal": None,
            "in_progress": True,
            "committed": 8,
            "completed": 8,
        },
    ]
    assert metrics.sprint_velocity(session, date(2026, 9, 27))[0]["in_progress"] is True
    assert metrics.sprint_velocity(session, date(2026, 10, 11))[1]["in_progress"] is True


def test_pr_cycle_time_by_sprint(session):
    _sprint(session, "Sprint 1", date(2026, 9, 14))
    _sprint(session, "Sprint 2", date(2026, 9, 28))
    start = datetime(2026, 9, 14)
    for hours in (10, 20, 30, 40, 50):
        _pr(session, start, start + timedelta(hours=hours))
    last_minute = datetime(2026, 9, 27, 23, 59)
    _pr(session, last_minute - timedelta(hours=30), last_minute)
    _pr(session, datetime(2026, 9, 27), datetime(2026, 9, 28))  # the next sprint's first moment
    _pr(session, start - timedelta(days=1), start - timedelta(seconds=1))  # before both
    _pr(session, start)  # open

    rows = metrics.pr_cycle_time_by_sprint(session, NOW)
    assert rows == [
        {
            "sprint": "Sprint 1",
            "start": date(2026, 9, 14),
            "in_progress": False,
            "median_hours": 30.0,
            "p85_hours": 42.5,
            "merged": 6,
        },
        {
            "sprint": "Sprint 2",
            "start": date(2026, 9, 28),
            "in_progress": True,
            "median_hours": 24.0,
            "p85_hours": 24.0,
            "merged": 1,
        },
    ]


def test_pr_cycle_time_by_week(session):
    monday = datetime(2026, 9, 21)
    _pr(session, monday - timedelta(hours=4), monday)  # first moment of the week
    _pr(session, monday, monday + timedelta(days=6, hours=22))  # its Sunday
    _pr(session, monday - timedelta(hours=3), monday - timedelta(seconds=3600))  # week before
    _pr(session, NOW - timedelta(hours=5), NOW - timedelta(hours=1))
    _pr(session, NOW - timedelta(days=60), NOW - timedelta(days=59))  # too old

    rows = metrics.pr_cycle_time(session, 3, NOW)
    assert rows == [
        {"week": date(2026, 9, 14), "median_hours": 2.0, "p85_hours": 2.0, "merged": 1},
        {"week": date(2026, 9, 21), "median_hours": 85.0, "p85_hours": 141.7, "merged": 2},
        {"week": date(2026, 9, 28), "median_hours": 4.0, "p85_hours": 4.0, "merged": 1},
    ]


def test_ci_health(session):
    this_week = datetime(2026, 9, 28, 9)
    last_week = datetime(2026, 9, 21, 9)
    _ci(session, this_week, "web")
    _ci(session, this_week, "web", "failure")
    _ci(session, this_week, "api")
    _ci(session, last_week, "api", "failure", flaky=True)
    _ci(session, last_week, "api")
    _ci(session, last_week, "api", "cancelled")
    _ci(session, datetime(2026, 9, 20, 23), "api", "failure")  # before the two weeks

    health = metrics.ci_health(session, 2, NOW)
    assert health == {
        "by_suite": [
            {"suite": "api", "runs": 4, "pass_rate": 50.0, "flaky_rate": 25.0},
            {"suite": "web", "runs": 2, "pass_rate": 50.0, "flaky_rate": 0.0},
        ],
        "by_week": [
            {"week": date(2026, 9, 21), "runs": 3, "pass_rate": 33.3},
            {"week": date(2026, 9, 28), "runs": 3, "pass_rate": 66.7},
        ],
    }


def test_quality_by_module(session):
    merged = NOW - timedelta(days=1)
    for caused in (True, False, False):
        _pr(session, merged, merged, module="billing_auth", caused_incident=caused)
    for caused in (True, False, False, False):
        _pr(session, merged, merged, module="leads", caused_incident=caused)
    _pr(session, merged, module="leads", caused_incident=True)  # open: left out
    _pr(session, merged, merged, module=None, caused_incident=True)  # no module: skipped
    _incident(session, merged, module="billing_auth")
    _incident(session, merged, module="billing_auth")
    _incident(session, merged, module="pipeline")  # no merged PRs: no row
    _issue(session, 3, merged, 4.0, module="leads")
    _issue(session, 4, merged, 3.0, module="leads")
    _issue(session, 5, None, 9.0, module="leads")  # open
    _issue(session, None, merged, 9.0, module="leads")  # not estimated
    _issue(session, 2, merged, None, module="billing_auth")  # no actual days

    assert metrics.quality_by_module(session) == [
        {
            "module": "billing_auth",
            "merged_prs": 3,
            "incidents": 2,
            "incident_rate": 33.3,
            "days_per_point": None,
        },
        {
            "module": "leads",
            "merged_prs": 4,
            "incidents": 0,
            "incident_rate": 25.0,
            "days_per_point": 1.0,
        },
    ]


def test_sources_counts_rows_per_source(session):
    _pr(session, NOW, source="synthetic")
    _pr(session, NOW, source="synthetic")
    _pr(session, NOW, source="github", external_id="9")
    _deploy(session, NOW, source="github", external_id="d1")
    _issue(session)

    counts = metrics.sources(session)
    assert counts["pull_requests"] == {"synthetic": 2, "github": 1}
    assert counts["deployments"] == {"synthetic": 0, "github": 1}
    assert counts["issues"] == {"synthetic": 1, "github": 0}
    assert counts["ci_runs"] == counts["incidents"] == {"synthetic": 0, "github": 0}


def test_the_simulated_history_shows_its_designed_patterns(session):
    now = datetime(2026, 10, 2, 15, 30)
    synth.build(session, now=now)
    session.commit()

    quality = {row["module"]: row for row in metrics.quality_by_module(session)}
    assert quality["forecasting"]["days_per_point"] > quality["leads"]["days_per_point"]
    assert metrics.quality_by_module(session)[0]["module"] == "billing_auth"

    suites = metrics.ci_health(session, 26, now)["by_suite"]
    flakiest = max(suites, key=lambda row: row["flaky_rate"])
    assert flakiest["suite"] == "integrations-e2e"

    summary = metrics.dora_summary(session, now)
    assert summary["current"]["deployments"] > 0
    assert summary["current"]["lead_time_hours"] is not None
    velocity = metrics.sprint_velocity(session, now.date())
    assert len(velocity) == 13
    assert [row["in_progress"] for row in velocity].count(True) == 1
    assert metrics.sources(session)["pull_requests"]["github"] == 0
