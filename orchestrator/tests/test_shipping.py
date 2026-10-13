from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from sdlc.db import Base
from sdlc.shipping import deploys, shipped_at, shipped_through
from sdlc.tables import Deployment, PullRequest
from tests.shipping_cases import CASES, IDS, MERGED, MERGES, SHIPPED, UNKNOWN


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _store(db, case):
    for number, (sha, merged_at) in enumerate(MERGES.items(), start=1):
        for source in ("github", "synthetic"):
            db.add(
                PullRequest(
                    source=source,
                    number=number,
                    title=f"PR {number}",
                    created_at=datetime(2026, 9, 20),
                    merged_at=merged_at,
                    merge_commit_sha=sha,
                )
            )
    for deploy in case.deploys:
        db.add(
            Deployment(
                source=deploy.source,
                sha=deploy.sha,
                version=deploy.sha[:7],
                deployed_at=deploy.at,
                status="success" if deploy.succeeded else "failure",
            )
        )
    db.flush()


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_the_shipping_rule(db, case):
    _store(db, case)
    assert shipped_at(MERGED, deploys(db, "github")) == case.shipped


def test_shipped_through_is_the_merge_of_the_deployed_commit_else_the_deploy_time(db):
    _store(db, CASES[0])
    at = datetime(2026, 10, 1)
    of_merge = Deployment(source="github", sha=SHIPPED, version="a", deployed_at=at)
    unknown = Deployment(source="github", sha=UNKNOWN, version="f", deployed_at=at)
    no_sha = Deployment(source="github", version="2026.1", deployed_at=datetime(2026, 10, 2))
    assert shipped_through(db, of_merge) == MERGED
    assert shipped_through(db, unknown) == at
    assert shipped_through(db, no_sha) == datetime(2026, 10, 2)


@pytest.mark.parametrize("case", CASES, ids=IDS)
def test_deploys_pairs_each_success_with_its_shipped_through(db, case):
    _store(db, case)
    stored = [row for row in db.query(Deployment) if row.source == "github"]
    expected = [
        (shipped_through(db, row), row.deployed_at) for row in stored if row.status == "success"
    ]
    assert sorted(deploys(db, "github")) == sorted(expected)


def test_an_unmerged_pr_never_ships():
    assert shipped_at(None, [(datetime(2026, 10, 1), datetime(2026, 10, 1))]) is None


def test_the_earliest_shipping_deploy_wins():
    pairs = [(MERGED, datetime(2026, 9, 28)), (MERGED, datetime(2026, 9, 26))]
    assert shipped_at(MERGED, pairs) == datetime(2026, 9, 26)
