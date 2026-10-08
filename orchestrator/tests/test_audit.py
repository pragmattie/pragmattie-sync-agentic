import inspect
import json
import re
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sdlc import agent_runs, audit
from sdlc.audit import (
    TRIAL,
    count_decisions,
    decisions_for,
    latest_decision,
    list_decisions,
    record_decision,
    serialize_decision,
)
from sdlc.db import Base

T0 = datetime(2026, 10, 5, 9, 0)


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _record(db, minute=0, *, agent="pr_risk_rubric", subject_id=1, trigger="poll", **fields):
    defaults = {"subject_type": "pr", "subject_source": "github", "head_sha": "a" * 40}
    return record_decision(
        db,
        agent=agent,
        agent_version="1.0",
        subject_id=subject_id,
        trigger=trigger,
        now=T0 + timedelta(minutes=minute),
        **{**defaults, **fields},
    )


def test_record_decision_flushes_without_committing(db):
    row = _record(db)

    assert row.id is not None
    assert row.attempt == 1
    assert row.status == "ok"
    assert row.created_at == T0
    db.rollback()
    assert count_decisions(db) == 0


def test_the_same_attempt_twice_is_rejected(db):
    _record(db)
    with pytest.raises(IntegrityError):
        _record(db, 1)


def test_a_second_attempt_is_accepted(db):
    _record(db)
    _record(db, 1, attempt=2)

    assert [row.attempt for row in decisions_for(db, "pr_risk_rubric", **_subject())] == [1, 2]


def _subject(head_sha="a" * 40, subject_id=1):
    return {
        "subject_type": "pr",
        "subject_source": "github",
        "subject_id": subject_id,
        "head_sha": head_sha,
    }


def test_decisions_for_is_one_version_oldest_first_without_trials(db):
    later = _record(db, 5, attempt=2)
    first = _record(db, 0)
    _record(db, 9, attempt=3, trigger=TRIAL)
    _record(db, 1, head_sha="b" * 40)
    _record(db, 2, subject_id=2)
    _record(db, 3, agent="reviewer")

    assert decisions_for(db, "pr_risk_rubric", **_subject()) == [first, later]


def test_decisions_for_matches_a_missing_head_sha(db):
    row = _record(db, head_sha=None)
    _record(db, 1)

    assert decisions_for(db, "pr_risk_rubric", **_subject(head_sha=None)) == [row]


def test_latest_decision_is_the_newest_ok_non_trial_row_at_any_version(db):
    _record(db, 0)
    newest_ok = _record(db, 5, head_sha="b" * 40)
    _record(db, 6, head_sha="c" * 40, status="error", error="timed out")
    _record(db, 7, head_sha="d" * 40, trigger=TRIAL)
    _record(db, 8, subject_id=2)

    subject = {"subject_type": "pr", "subject_source": "github", "subject_id": 1}
    assert latest_decision(db, "pr_risk_rubric", **subject) == newest_ok
    assert latest_decision(db, "reviewer", **subject) is None


def test_trial_rows_are_listed_and_counted_but_not_read_as_decisions(db):
    real = _record(db, 0)
    trial = _record(db, 1, trigger=TRIAL, attempt=2)

    assert list_decisions(db) == [trial, real]
    assert count_decisions(db) == 2
    assert decisions_for(db, "pr_risk_rubric", **_subject()) == [real]
    subject = {"subject_type": "pr", "subject_source": "github", "subject_id": 1}
    assert latest_decision(db, "pr_risk_rubric", **subject) == real


@pytest.fixture
def mixed(db):
    """Six decisions differing in agent, subject type, source, status and tier."""
    rows = [
        _record(db, 0, tier="T1"),
        _record(db, 1, subject_id=2, tier="T3", status="error", error="boom"),
        _record(db, 2, agent="reviewer", tier="T1"),
        _record(db, 3, subject_type="issue", subject_source="github", agent="implementer"),
        _record(db, 4, subject_id=3, subject_source="synthetic", tier="T3"),
        _record(db, 5, agent="reviewer", subject_id=4, status="error", tier="T1"),
    ]
    db.commit()
    return rows


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, [5, 4, 3, 2, 1, 0]),
        ({"agent": "reviewer"}, [5, 2]),
        ({"subject_type": "issue"}, [3]),
        ({"subject_source": "synthetic"}, [4]),
        ({"status": "error"}, [5, 1]),
        ({"tier": "T3"}, [4, 1]),
        ({"agent": "pr_risk_rubric", "tier": "T3"}, [4, 1]),
        ({"agent": "pr_risk_rubric", "tier": "T3", "status": "ok"}, [4]),
        ({"agent": "reviewer", "status": "error", "tier": "T1"}, [5]),
        ({"subject_type": "pr", "subject_source": "github", "tier": "T1"}, [5, 2, 0]),
        ({"agent": "implementer", "tier": "T1"}, []),
    ],
)
def test_filters_alone_and_combined(db, mixed, filters, expected):
    assert list_decisions(db, **filters) == [mixed[i] for i in expected]
    assert count_decisions(db, **filters) == len(expected)


def test_pagination_pages_newest_first_and_the_count_ignores_it(db, mixed):
    assert list_decisions(db, limit=2) == [mixed[5], mixed[4]]
    assert list_decisions(db, limit=2, offset=2) == [mixed[3], mixed[2]]
    assert list_decisions(db, limit=2, offset=4) == [mixed[1], mixed[0]]
    assert list_decisions(db, limit=2, offset=6) == []
    assert list_decisions(db, agent="pr_risk_rubric", limit=1, offset=1) == [mixed[1]]
    assert count_decisions(db, agent="pr_risk_rubric") == 3


def test_list_decisions_defaults_to_fifty(db):
    for minute in range(55):
        _record(db, minute, subject_id=minute)

    assert len(list_decisions(db)) == 50
    assert count_decisions(db) == 55


def test_serialize_decision_gives_every_column_json_safe(db):
    original = _record(db, tier="T2", final_score=42, signals={"change_size": 20})
    row = _record(
        db,
        1,
        attempt=2,
        tier="T1",
        final_score=30,
        output={"reasons": ["Risk score 30 is in the T1 band."]},
        supersedes_id=original.id,
    )
    db.commit()

    data = serialize_decision(row)

    assert set(data) == {column.key for column in row.__table__.columns}
    assert data["created_at"] == "2026-10-05T09:01:00"
    assert data["supersedes_id"] == original.id
    assert data["output"] == {"reasons": ["Risk score 30 is in the T1 band."]}
    assert data["status"] == "ok"
    assert json.loads(json.dumps(data)) == data


@pytest.mark.parametrize("module", [audit, agent_runs])
def test_the_module_never_updates_or_deletes_a_row(module):
    source = inspect.getsource(module)

    assert not re.search(r"\b(update|delete)\(", source, re.I)


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset is Unix-only")
def test_record_decisions_default_time_is_utc_whatever_the_local_zone(db, monkeypatch):
    from datetime import UTC

    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        row = record_decision(
            db,
            agent="pr_risk",
            agent_version="v1",
            subject_type="pr",
            subject_source="github",
            subject_id=1,
            trigger="poll",
        )
        utc = datetime.now(UTC).replace(tzinfo=None)
    finally:
        monkeypatch.undo()
        time.tzset()
    assert abs(row.created_at - utc) < timedelta(seconds=2)
    assert row.created_at.microsecond == 0
