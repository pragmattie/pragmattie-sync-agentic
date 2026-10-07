from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sdlc.audit import TRIAL, record_decision
from sdlc.db import Base

T0 = datetime(2026, 10, 5, 9, 0)
URL = "/api/v1/signals/decisions"


def _record(db, minute, *, agent="pr_risk_rubric", subject_id=1, trigger="poll", **fields):
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


def _llm(input_tokens, output_tokens, cost=None):
    output = {"cost_usd": cost} if cost is not None else {"reasons": ["No model call."]}
    return {"input_tokens": input_tokens, "output_tokens": output_tokens, "output": output}


@pytest.fixture
def ids(client, sqlite_engine):
    """Seven decisions differing in agent, subject, source, status, tier, tokens and cost.

    Returns their ids in the order they were made, oldest first.
    """
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        rows = [
            _record(db, 0, tier="T1", **_llm(1000, 200, 0.0123)),
            _record(db, 1, subject_id=2, tier="T3", status="error", error="boom"),
            _record(db, 2, agent="reviewer", tier="T1", **_llm(3000, 500, 0.04561)),
            _record(db, 3, agent="implementer", subject_type="issue", **_llm(9000, 4000, 0.5)),
            _record(db, 4, subject_id=3, subject_source="synthetic", tier="T3"),
            _record(db, 5, agent="reviewer", subject_id=4, status="timeout", tier="T1"),
            _record(
                db,
                6,
                agent="triage",
                subject_type="issue",
                subject_id=7,
                trigger=TRIAL,
                **_llm(500, 100, 0.00337),
            ),
        ]
        db.commit()
        return [row.id for row in rows]


def _ids(body):
    return [row["id"] for row in body["decisions"]]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({}, [6, 5, 4, 3, 2, 1, 0]),
        ({"agent": "reviewer"}, [5, 2]),
        ({"subject_type": "issue"}, [6, 3]),
        ({"subject_source": "synthetic"}, [4]),
        ({"status": "error"}, [1]),
        ({"status": "timeout"}, [5]),
        ({"tier": "T3"}, [4, 1]),
        ({"agent": "pr_risk_rubric", "tier": "T3"}, [4, 1]),
        ({"agent": "pr_risk_rubric", "tier": "T3", "status": "ok"}, [4]),
        ({"subject_type": "pr", "subject_source": "github", "tier": "T1"}, [5, 2, 0]),
        ({"agent": "implementer", "tier": "T1"}, []),
    ],
)
def test_filters_alone_and_combined(client, ids, query, expected):
    response = client.get(URL, params=query)

    assert response.status_code == 200
    body = response.json()
    assert _ids(body) == [ids[i] for i in expected]
    assert body["total"] == len(expected)
    assert body["totals"]["runs"] == len(expected)


def test_pagination_pages_newest_first_and_total_is_the_whole_set(client, ids):
    pages = [client.get(URL, params={"limit": 3, "offset": n}).json() for n in (0, 3, 6, 9)]

    assert [_ids(page) for page in pages] == [
        [ids[6], ids[5], ids[4]],
        [ids[3], ids[2], ids[1]],
        [ids[0]],
        [],
    ]
    for page, offset in zip(pages, (0, 3, 6, 9), strict=True):
        assert (page["total"], page["limit"], page["offset"]) == (7, 3, offset)


def test_defaults_are_fifty_from_the_start(client, ids):
    body = client.get(URL).json()

    assert (body["limit"], body["offset"], body["total"]) == (50, 0, 7)


def test_totals_match_a_hand_calculation_over_trial_and_non_trial_rows(client, ids):
    totals = client.get(URL, params={"limit": 1}).json()["totals"]

    assert totals == {
        "runs": 7,
        "input_tokens": 1000 + 3000 + 9000 + 500,
        "output_tokens": 200 + 500 + 4000 + 100,
        "cost_usd": round(0.0123 + 0.04561 + 0.5 + 0.00337, 4),  # 0.5613
        "runs_without_cost": 3,
    }
    assert totals["cost_usd"] == 0.5613


def test_totals_cover_the_filtered_set_not_the_page(client, ids):
    body = client.get(URL, params={"agent": "reviewer", "limit": 1, "offset": 1}).json()

    assert _ids(body) == [ids[2]]
    assert body["totals"] == {
        "runs": 2,
        "input_tokens": 3000,
        "output_tokens": 500,
        "cost_usd": 0.0456,
        "runs_without_cost": 1,
    }


def test_rows_without_a_cost_are_counted_and_add_nothing(client, ids):
    totals = client.get(URL, params={"agent": "pr_risk_rubric"}).json()["totals"]

    assert totals == {
        "runs": 3,
        "input_tokens": 1000,
        "output_tokens": 200,
        "cost_usd": 0.0123,
        "runs_without_cost": 2,
    }


def test_an_empty_set_totals_zero(client, ids):
    totals = client.get(URL, params={"agent": "nobody"}).json()["totals"]

    assert totals == {
        "runs": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "runs_without_cost": 0,
    }


@pytest.mark.parametrize(
    "query",
    [
        {"limit": 0},
        {"limit": 201},
        {"offset": -1},
        {"subject_type": "account"},
        {"subject_source": "manual"},
    ],
)
def test_out_of_range_queries_are_rejected(client, ids, query):
    assert client.get(URL, params=query).status_code == 422


def test_limit_200_is_accepted(client, ids):
    assert client.get(URL, params={"limit": 200}).status_code == 200


def test_agents_are_listed_most_runs_first(client, ids):
    response = client.get(f"{URL}/agents")

    assert response.status_code == 200
    assert response.json() == [
        {"agent": "pr_risk_rubric", "runs": 3},
        {"agent": "reviewer", "runs": 2},
        {"agent": "implementer", "runs": 1},
        {"agent": "triage", "runs": 1},
    ]


def test_agents_is_empty_without_rows(client, sqlite_engine):
    Base.metadata.create_all(sqlite_engine)

    assert client.get(f"{URL}/agents").json() == []


def test_a_single_decision(client, ids):
    response = client.get(f"{URL}/{ids[2]}")

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == ids[2]
    assert body["agent"] == "reviewer"
    assert body["created_at"] == "2026-10-05T09:02:00"
    assert body["output"] == {"cost_usd": 0.04561}
    assert body == client.get(URL, params={"agent": "reviewer"}).json()["decisions"][1]


def test_a_missing_decision_is_404(client, ids):
    response = client.get(f"{URL}/999")

    assert response.status_code == 404
    assert response.json() == {"detail": "No decision 999."}
