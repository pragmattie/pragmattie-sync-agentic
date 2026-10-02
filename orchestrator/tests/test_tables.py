from datetime import date, datetime
from itertools import count

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sdlc.db import Base
from sdlc.tables import (
    MODULES,
    SOURCES,
    CIRun,
    Deployment,
    Engineer,
    Incident,
    Issue,
    PullRequest,
    Sprint,
)

OPENED = datetime(2026, 9, 1, 9, 0)
_unique = count()


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _engineer(session, login="ada", source="synthetic"):
    engineer = Engineer(login=login, name="Ada Example", role="backend", source=source)
    session.add(engineer)
    session.flush()
    return engineer


def _issue(session, external_id="issue-1", source="synthetic"):
    issue = Issue(external_id=external_id, source=source, title="Lead scoring", created_at=OPENED)
    session.add(issue)
    session.flush()
    return issue


def test_one_row_of_each_kind_reads_back_with_its_relationships(session):
    ada = _engineer(session)
    sprint = Sprint(name="Sprint 1", start_date=date(2026, 9, 1), end_date=date(2026, 9, 14))
    session.add(sprint)
    session.flush()
    issue = Issue(
        external_id="issue-12",
        number=12,
        title="Lead scoring",
        module="leads",
        priority="p2",
        estimate_points=3,
        created_at=OPENED,
        sprint_id=sprint.id,
        assignee=ada,
        epic="Lead management",
    )
    pull_request = PullRequest(
        external_id="pr-34",
        number=34,
        title="Add lead scoring",
        author=ada,
        issue=issue,
        module="leads",
        created_at=OPENED,
    )
    ci_run = CIRun(
        external_id="run-56",
        pull_request=pull_request,
        suite="api",
        conclusion="success",
        started_at=OPENED,
        duration_seconds=240,
    )
    deployment = Deployment(external_id="deploy-7", version="1.4.0", deployed_at=OPENED)
    incident = Incident(
        external_id="incident-8",
        title="Lead scores missing",
        severity="sev2",
        module="leads",
        opened_at=OPENED,
        caused_by_pr=pull_request,
        deployment=deployment,
    )
    session.add_all([ci_run, incident])
    session.commit()
    session.expire_all()

    issue = session.scalars(select(Issue)).one()
    assert issue.assignee.login == "ada"
    assert issue.sprint_id == session.scalars(select(Sprint.id)).one()
    assert (issue.type, issue.state, issue.source) == ("feature", "open", "synthetic")

    pull_request = session.scalars(select(PullRequest)).one()
    assert pull_request.author.login == "ada"
    assert pull_request.issue.external_id == "issue-12"
    assert pull_request.state == "open"
    assert pull_request.modules_touched == 1
    assert pull_request.files_changed == pull_request.review_count == 0
    assert pull_request.touches_migration is False
    assert pull_request.caused_incident is False
    assert pull_request.reverted is False

    ci_run = session.scalars(select(CIRun)).one()
    assert ci_run.pull_request.number == 34
    assert ci_run.flaky is False

    deployment = session.scalars(select(Deployment)).one()
    assert (deployment.status, deployment.pr_count) == ("success", 0)

    incident = session.scalars(select(Incident)).one()
    assert incident.caused_by_pr.external_id == "pr-34"
    assert incident.deployment.version == "1.4.0"


def _ci_run(session, external_id, source):
    return CIRun(
        external_id=external_id,
        source=source,
        suite="api",
        conclusion="success",
        started_at=OPENED,
    )


def _deployment(session, external_id, source):
    return Deployment(external_id=external_id, source=source, version="1.0.0", deployed_at=OPENED)


def _incident(session, external_id, source):
    return Incident(
        external_id=external_id, source=source, title="Outage", severity="sev1", opened_at=OPENED
    )


def _issue_row(session, external_id, source):
    return Issue(external_id=external_id, source=source, title="Item", created_at=OPENED)


def _pull_request_row(session, external_id, source):
    n = next(_unique)
    return PullRequest(
        external_id=external_id,
        source=source,
        number=1,
        title="Change",
        author_id=_engineer(session, login=f"dev-{n}", source=source).id,
        issue_id=_issue(session, external_id=f"issue-{n}", source=source).id,
        module="platform",
        created_at=OPENED,
    )


ROW_FACTORIES = {
    "issues": _issue_row,
    "pull_requests": _pull_request_row,
    "ci_runs": _ci_run,
    "deployments": _deployment,
    "incidents": _incident,
}


@pytest.mark.parametrize("make_row", ROW_FACTORIES.values(), ids=ROW_FACTORIES.keys())
def test_external_id_is_unique_within_a_source(session, make_row):
    session.add(make_row(session, "item-1", "synthetic"))
    session.add(make_row(session, "item-1", "github"))
    session.commit()

    session.add(make_row(session, "item-1", "github"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_engineer_login_is_unique_within_a_source(session):
    session.add_all(
        [Engineer(login="ada", name="Ada"), Engineer(login="ada", name="Ada", source="github")]
    )
    session.commit()

    session.add(Engineer(login="ada", name="Ada"))
    with pytest.raises(IntegrityError):
        session.commit()


def test_every_table_is_prefixed_and_defaults_to_synthetic():
    tables = Base.metadata.tables.values()
    assert {table.name for table in tables} >= {
        "sdlc_engineers",
        "sdlc_sprints",
        "sdlc_issues",
        "sdlc_pull_requests",
        "sdlc_ci_runs",
        "sdlc_deployments",
        "sdlc_incidents",
    }
    for table in tables:
        assert table.name.startswith("sdlc_")
        source = table.columns["source"]
        assert source.default.arg == "synthetic"
        assert source.server_default.arg == "synthetic"
        assert source.default.arg in SOURCES
        assert source.type.length == 20


def test_constants():
    assert SOURCES == ("synthetic", "github")
    assert MODULES == (
        "leads",
        "accounts",
        "pipeline",
        "forecasting",
        "integrations",
        "billing_auth",
        "orchestrator",
        "platform",
    )
