from datetime import datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc import gate_status
from sdlc.agents.gate import Approvals, evaluate
from sdlc.db import Base
from sdlc.tables import GateStatus, PullRequest
from sdlc.tiers import load_policy

POLICY = load_policy()


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _pr(db, number):
    pr = PullRequest(
        source="github",
        external_id=str(number),
        number=number,
        title=f"PR {number}",
        created_at=datetime(2026, 10, 6, 8, 0),
    )
    db.add(pr)
    db.flush()
    return pr


def test_upsert_adds_the_first_row_without_committing(db):
    pr = _pr(db, 170)
    gate = evaluate(POLICY, "T3", ok=True, approvals=Approvals(), mode="shadow")

    row = gate_status.upsert(db, pr, gate, tier="T3", mode="shadow", now=datetime(2026, 10, 6, 9))

    assert row.id is not None
    assert row.state == "success"
    assert row.would_be == "pending"
    assert row.missing == ["human sign-off", "manual QA", "simulated second approval"]
    assert row.description == gate.description
    assert row.updated_at == datetime(2026, 10, 6, 9)
    assert db.in_transaction()


def test_upsert_replaces_rather_than_appends(db):
    pr, other = _pr(db, 170), _pr(db, 171)
    pending = evaluate(POLICY, "T1", ok=True, approvals=Approvals(), mode="enforce")
    met = evaluate(POLICY, "T1", ok=True, approvals=Approvals(signoff=True), mode="enforce")
    gate_status.upsert(db, other, pending, tier="T1", mode="enforce")
    first = gate_status.upsert(db, pr, pending, tier="T1", mode="enforce")
    db.commit()

    second = gate_status.upsert(
        db, pr, met, tier="T1", mode="enforce", now=datetime(2026, 10, 6, 10, 30, 15, 999)
    )
    db.commit()

    assert second.id == first.id
    assert db.scalar(select(func.count()).select_from(GateStatus)) == 2
    row = db.scalar(select(GateStatus).where(GateStatus.pull_request_id == pr.id))
    assert (row.state, row.would_be, row.missing) == ("success", "success", [])
    assert row.description == "T1: requirements met"
    assert row.updated_at == datetime(2026, 10, 6, 10, 30, 15)
    untouched = db.scalar(select(GateStatus).where(GateStatus.pull_request_id == other.id))
    assert untouched.missing == ["human sign-off"]
