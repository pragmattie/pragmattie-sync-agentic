from datetime import datetime

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc.db import Base
from sdlc.github_client import GitHubClient, GitHubError
from sdlc.signals import github
from sdlc.signals.github import Collector, labels_of, linked_issue_number, suite_of
from sdlc.tables import CIRun, Engineer, Issue, PullRequest

REPO = "acme/widgets"
API = f"/repos/{REPO}"


def _label(*names):
    return [{"id": index, "name": name} for index, name in enumerate(names)]


ISSUES = [
    {
        "number": 12,
        "title": "Lead scoring rules",
        "state": "closed",
        "labels": _label("module:leads", "type:bug", "priority:p1", "points:5", "epic:Scoring"),
        "created_at": "2026-09-01T09:00:00Z",
        "closed_at": "2026-09-04T21:00:00Z",
        "assignee": {"login": "ada-dev"},
    },
    {
        "number": 13,
        "title": "Forecast export",
        "state": "open",
        "labels": _label("points:big"),
        "created_at": "2026-09-02T09:00:00Z",
        "closed_at": None,
        "assignee": None,
    },
    {
        "number": 14,
        "title": "A pull request in the issues list",
        "state": "open",
        "labels": [],
        "created_at": "2026-09-02T09:00:00Z",
        "pull_request": {"url": "https://api.github.com/repos/acme/widgets/pulls/14"},
    },
    {
        "number": 15,
        "title": "Login is down",
        "state": "closed",
        "labels": _label("incident"),
        "created_at": "2026-09-03T09:00:00Z",
        "closed_at": "2026-09-03T10:00:00Z",
    },
]

PULLS = {
    20: {
        "number": 20,
        "title": "Add lead scoring",
        "body": "Some text.\n\nFixed #12",
        "state": "closed",
        "labels": _label("caused-incident"),
        "user": {"login": "ada-dev"},
        "created_at": "2026-09-02T10:00:00Z",
        "merged_at": "2026-09-03T10:00:00Z",
        "closed_at": "2026-09-03T10:00:00Z",
        "merge_commit_sha": "a" * 40,
        "changed_files": 3,
        "additions": 120,
        "deletions": 8,
        "commits": 4,
    },
    21: {
        "number": 21,
        "title": 'revert "Add lead scoring"',
        "body": None,
        "state": "open",
        "labels": _label("module:forecasting"),
        "user": {"login": "bo-dev"},
        "created_at": "2026-09-05T10:00:00Z",
        "merged_at": None,
        "closed_at": None,
        "merge_commit_sha": "b" * 40,
        "changed_files": 1,
        "additions": 0,
        "deletions": 120,
        "commits": 0,
    },
}
FILES = {
    20: [
        {"filename": "apps/api/app/routers/leads.py"},
        {"filename": "apps/api/tests/test_leads.py"},
        {"filename": "apps/api/alembic/versions/0003_lead_score.py"},
    ],
    21: [{"filename": "apps/api/app/routers/leads.py"}],
}
REVIEWS = {
    20: [
        {"id": 1, "state": "PENDING", "submitted_at": None},
        {"id": 2, "state": "COMMENTED", "submitted_at": "2026-09-02T13:00:00Z"},
        {"id": 3, "state": "APPROVED", "submitted_at": "2026-09-02T12:15:00Z"},
    ],
    21: [],
}

RUNS = [
    {"id": 500, "path": ".github/workflows/ci.yml", "pull_requests": [{"number": 20}]},
    {"id": 501, "path": ".github/workflows/deploy.yml", "pull_requests": []},
    {"id": 502, "path": ".github/workflows/ci.yml", "pull_requests": [{"number": 99}]},
]


def _job(job_id, name, conclusion, attempt=1, status="completed", seconds=95):
    return {
        "id": job_id,
        "name": name,
        "status": status,
        "conclusion": conclusion,
        "run_attempt": attempt,
        "started_at": "2026-09-02T10:05:00Z",
        "completed_at": f"2026-09-02T10:0{5 + seconds // 60}:{seconds % 60:02d}Z",
    }


JOBS = {
    500: [
        _job(700, "CRM API (lint + tests)", "failure", attempt=1),
        _job(701, "CRM web (tests + build)", "success", attempt=1),
        _job(702, "CRM API (lint + tests)", "success", attempt=2),
        _job(703, "Orchestrator (lint + tests)", None, attempt=2, status="in_progress"),
    ],
    501: [_job(710, "Deploy", "success")],
    502: [
        _job(720, "Migrations on MySQL", "failure"),
        _job(721, "A very long custom job name that goes on and on and on", "success"),
    ],
}


def _handler(request):
    path = request.url.path
    if path == f"{API}/issues":
        return httpx.Response(200, json=ISSUES)
    if path == f"{API}/pulls":
        return httpx.Response(200, json=[{"number": number} for number in PULLS])
    if path == f"{API}/actions/runs":
        return httpx.Response(200, json={"total_count": len(RUNS), "workflow_runs": RUNS})
    parts = path.removeprefix(f"{API}/").split("/")
    if parts[0] == "pulls":
        number = int(parts[1])
        if len(parts) == 2:
            return httpx.Response(200, json=PULLS[number])
        return httpx.Response(200, json={"files": FILES, "reviews": REVIEWS}[parts[2]][number])
    if parts[:2] == ["actions", "runs"] and parts[3] == "jobs":
        assert request.url.params["filter"] == "all"
        if int(parts[2]) == 501:
            raise AssertionError("deploy runs are never fetched")
        return httpx.Response(200, json={"jobs": JOBS[int(parts[2])]})
    return httpx.Response(404, json={"message": "Not Found"})


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


@pytest.fixture
def client():
    return GitHubClient(token="test-token", repo=REPO, transport=httpx.MockTransport(_handler))


@pytest.fixture
def collected(session, client):
    counts = Collector(session, client).run()
    return session, counts


def _github(session, table, external_id):
    return session.scalar(
        select(table).where(table.source == "github", table.external_id == external_id)
    )


def test_labels_of():
    item = {"labels": _label("module:leads", "points: 3", "incident", "epic:Big: one")}
    assert labels_of(item) == {
        "module": "leads",
        "points": "3",
        "incident": "true",
        "epic": "Big: one",
    }
    assert labels_of({}) == {}


@pytest.mark.parametrize(
    ("body", "number"),
    [
        ("Closes #4", 4),
        ("closed #5", 5),
        ("This FIXES #6.", 6),
        ("fix #7", 7),
        ("Resolved: #8", 8),
        ("resolve #9", 9),
        ("See #10", None),
        (None, None),
    ],
)
def test_linked_issue_number(body, number):
    assert linked_issue_number(body) == number


def test_suite_of():
    assert suite_of("CRM API (lint + tests)") == "api"
    assert suite_of("CRM web (tests + build)") == "web"
    assert suite_of("Insights web (tests + build)") == "insights-web"
    assert suite_of("Orchestrator (lint + tests)") == "orchestrator"
    assert suite_of("Migrations on MySQL") == "migrations"
    assert suite_of("x" * 50) == "x" * 40


def test_run_returns_the_counts(collected):
    _, counts = collected
    assert counts == {"issues": 2, "pull_requests": 2, "ci_jobs": 5}


def test_issue_fields(collected):
    session, _ = collected
    issue = _github(session, Issue, "issue-12")
    assert issue.number == 12
    assert issue.title == "Lead scoring rules"
    assert issue.module == "leads"
    assert issue.type == "bug"
    assert issue.priority == "p1"
    assert issue.epic == "Scoring"
    assert issue.estimate_points == 5
    assert issue.state == "closed"
    assert issue.created_at == datetime(2026, 9, 1, 9, 0)
    assert issue.closed_at == datetime(2026, 9, 4, 21, 0)
    assert issue.actual_days == 3.5
    assert issue.assignee.login == "ada-dev"
    assert issue.assignee.name == "ada-dev"
    assert issue.assignee.source == "github"


def test_open_issue_defaults(collected):
    session, _ = collected
    issue = _github(session, Issue, "issue-13")
    assert issue.type == "feature"
    assert issue.module is None
    assert issue.estimate_points is None
    assert issue.state == "open"
    assert issue.closed_at is None
    assert issue.actual_days is None
    assert issue.assignee is None


def test_pull_requests_and_incidents_in_the_issues_list_are_skipped(collected):
    session, _ = collected
    assert _github(session, Issue, "issue-14") is None
    assert _github(session, Issue, "issue-15") is None


def test_merged_pull_request_fields(collected):
    session, _ = collected
    pr = _github(session, PullRequest, "pr-20")
    assert pr.number == 20
    assert pr.title == "Add lead scoring"
    assert pr.author.login == "ada-dev"
    assert pr.issue.external_id == "issue-12"
    assert pr.module == "leads"
    assert pr.files_changed == 3
    assert pr.additions == 120
    assert pr.deletions == 8
    assert pr.touches_migration is True
    assert pr.touches_governance is False
    assert pr.test_files_changed == 1
    assert pr.docs_only is False
    assert pr.modules_touched == 1
    assert pr.review_count == 2
    assert pr.first_review_hours == 2.2
    assert pr.rework_commits == 3
    assert pr.state == "merged"
    assert pr.created_at == datetime(2026, 9, 2, 10, 0)
    assert pr.merged_at == datetime(2026, 9, 3, 10, 0)
    assert pr.merge_commit_sha == "a" * 40
    assert pr.closed_at == datetime(2026, 9, 3, 10, 0)
    assert pr.caused_incident is True
    assert pr.reverted is False


def test_open_revert_pull_request_fields(collected):
    session, _ = collected
    pr = _github(session, PullRequest, "pr-21")
    assert pr.module == "forecasting"
    assert pr.issue is None
    assert pr.review_count == 0
    assert pr.first_review_hours is None
    assert pr.rework_commits == 0
    assert pr.state == "open"
    assert pr.merged_at is None
    assert pr.merge_commit_sha is None
    assert pr.closed_at is None
    assert pr.caused_incident is False
    assert pr.reverted is True


def test_one_engineer_per_login(collected):
    session, _ = collected
    logins = session.scalars(select(Engineer.login).where(Engineer.source == "github")).all()
    assert sorted(logins) == ["ada-dev", "bo-dev"]


def test_ci_jobs(collected):
    session, _ = collected
    failed = _github(session, CIRun, "job-700")
    assert failed.suite == "api"
    assert failed.conclusion == "failure"
    assert failed.flaky is True
    assert failed.pull_request.external_id == "pr-20"
    assert failed.started_at == datetime(2026, 9, 2, 10, 5)
    assert failed.duration_seconds == 95

    assert _github(session, CIRun, "job-701").flaky is False
    assert _github(session, CIRun, "job-702").flaky is False
    assert _github(session, CIRun, "job-703") is None  # not finished

    migrations = _github(session, CIRun, "job-720")
    assert migrations.suite == "migrations"
    assert migrations.flaky is False  # never passed on a later attempt
    assert migrations.pull_request is None  # its pull request isn't stored
    assert _github(session, CIRun, "job-721").suite == "A very long custom job name that goes on"


def test_deploy_workflow_runs_are_skipped(collected):
    session, _ = collected
    assert _github(session, CIRun, "job-710") is None


def test_rerunning_updates_rows_in_place(collected, client):
    session, _ = collected
    before = {
        table: session.scalar(select(func.count()).select_from(table))
        for table in (Issue, PullRequest, CIRun, Engineer)
    }
    original = ISSUES[0]["title"]
    ISSUES[0]["title"] = "Lead scoring rules, renamed"
    try:
        Collector(session, client).run()
    finally:
        ISSUES[0]["title"] = original
    after = {
        table: session.scalar(select(func.count()).select_from(table))
        for table in (Issue, PullRequest, CIRun, Engineer)
    }
    assert after == before
    assert _github(session, Issue, "issue-12").title == "Lead scoring rules, renamed"


def test_synthetic_rows_with_the_same_number_are_untouched(session, client):
    synthetic = Issue(
        source="synthetic",
        external_id="syn-issue-12",
        number=12,
        title="Simulated work",
        state="open",
        created_at=datetime(2026, 1, 1),
    )
    clash = Issue(
        source="synthetic",
        external_id="issue-12",
        number=12,
        title="Simulated work with a clashing id",
        state="open",
        created_at=datetime(2026, 1, 1),
    )
    session.add_all([synthetic, clash])
    session.commit()

    Collector(session, client).run()

    for row, title in ((synthetic, "Simulated work"), (clash, "Simulated work with a clashing id")):
        session.refresh(row)
        assert row.source == "synthetic"
        assert row.title == title
        assert row.state == "open"
    assert _github(session, Issue, "issue-12").title == "Lead scoring rules"
    assert _github(session, PullRequest, "pr-20").issue.source == "github"


def test_cli_prints_the_counts(sqlite_engine, client, capsys):
    Base.metadata.create_all(sqlite_engine)
    assert github.main(engine=sqlite_engine, client=client) == 0
    assert capsys.readouterr().out.strip() == (
        f"Collected from {REPO}: issues: 2, pull_requests: 2, ci_jobs: 5"
    )


def test_cli_exits_with_the_friendly_error(monkeypatch, sqlite_engine):
    def missing_settings():
        raise GitHubError("Set GITHUB_TOKEN and GITHUB_REPO (owner/name) in your .env file first.")

    monkeypatch.setattr(github, "GitHubClient", missing_settings)
    with pytest.raises(SystemExit) as raised:
        github.main(engine=sqlite_engine)
    assert raised.value.code == (
        "Set GITHUB_TOKEN and GITHUB_REPO (owner/name) in your .env file first."
    )


def test_cli_exits_with_a_github_error(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    failing = GitHubClient(
        token="test-token",
        repo=REPO,
        transport=httpx.MockTransport(
            lambda request: httpx.Response(404, json={"message": "Nope"})
        ),
    )
    with pytest.raises(SystemExit) as raised:
        github.main(engine=sqlite_engine, client=failing)
    assert raised.value.code == f"GitHub 404 on {API}/issues: Nope"
