from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from app.models import Lead

VALID_LEAD = {
    "first_name": "Riley",
    "last_name": "Morgan",
    "email": "riley.morgan@fabrikam.example",
    "company": "Fabrikam Logistics",
}

SORT_MESSAGE = "sort must be one of ['company', 'created_at', 'last_name', 'score', 'status']"


@pytest.fixture
def make_lead(client):
    def make(**overrides):
        response = client.post("/api/v1/leads", json={**VALID_LEAD, **overrides})
        assert response.status_code == 201, response.text
        return response.json()

    return make


@pytest.fixture
def insert_lead(sqlite_engine):
    """Insert a lead directly, for states the API cannot create (dates, conversion)."""

    def insert(**overrides):
        values = {**VALID_LEAD, "source": "web", **overrides}
        with Session(sqlite_engine, expire_on_commit=False) as session:
            lead = Lead(**values)
            session.add(lead)
            session.commit()
            return lead.id

    return insert


@pytest.fixture
def sortable_leads(insert_lead):
    """Four leads whose order differs for every sort field. Returns their ids."""
    rows = [
        ("Dunn", "Cedar", 40, "working", datetime(2026, 1, 3)),
        ("Adams", "Delta", 90, "new", datetime(2026, 1, 1)),
        ("Clark", "Alder", 10, "qualified", datetime(2026, 1, 4)),
        ("Baker", "Birch", 70, "disqualified", datetime(2026, 1, 2)),
    ]
    return [
        insert_lead(last_name=last, company=company, score=score, status=status, created_at=at)
        for last, company, score, status, at in rows
    ]


def _ids(response):
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_create_lead_returns_the_full_shape(client, reps):
    owner = reps["zoe"]
    response = client.post(
        "/api/v1/leads",
        json={
            **VALID_LEAD,
            "title": "Head of Ops",
            "source": "event",
            "score": 55,
            "owner_id": owner.id,
        },
    )

    assert response.status_code == 201
    body = response.json()
    datetime.fromisoformat(body.pop("created_at"))
    assert isinstance(body.pop("id"), int)
    assert body == {
        **VALID_LEAD,
        "title": "Head of Ops",
        "source": "event",
        "status": "new",
        "score": 55,
        "owner_id": owner.id,
        "owner": {"id": owner.id, "name": "Zoe Park"},
        "converted_account_id": None,
    }


def test_create_lead_defaults(make_lead):
    lead = make_lead()

    assert lead["source"] == "web"
    assert lead["status"] == "new"
    assert lead["score"] == 0
    assert lead["title"] is None
    assert lead["owner"] is None


@pytest.mark.parametrize("status", ["qualified", "converted", "bogus"])
def test_create_lead_ignores_a_sent_status(make_lead, status):
    assert make_lead(status=status)["status"] == "new"


def test_create_lead_with_unknown_owner_is_404(client):
    response = client.post("/api/v1/leads", json={**VALID_LEAD, "owner_id": 9})

    assert response.status_code == 404
    assert response.json() == {"detail": "Rep 9 not found"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", ""),
        ("first_name", "x" * 81),
        ("last_name", ""),
        ("last_name", "x" * 81),
        ("email", "not-an-email"),
        ("company", ""),
        ("company", "x" * 201),
        ("title", "x" * 121),
        ("source", "cold-call"),
        ("score", -1),
        ("score", 101),
    ],
)
def test_create_lead_rejects_values_outside_the_limits(client, field, value):
    response = client.post("/api/v1/leads", json={**VALID_LEAD, field: value})

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", "x"),
        ("first_name", "x" * 80),
        ("last_name", "x" * 80),
        ("company", "x" * 200),
        ("title", "x" * 120),
        ("score", 0),
        ("score", 100),
    ],
)
def test_create_lead_accepts_values_at_the_limits(client, field, value):
    response = client.post("/api/v1/leads", json={**VALID_LEAD, field: value})

    assert response.status_code == 201
    assert response.json()[field] == value


@pytest.mark.parametrize("field", ["first_name", "last_name", "email", "company"])
def test_create_lead_requires_field(client, field):
    payload = {key: value for key, value in VALID_LEAD.items() if key != field}

    assert client.post("/api/v1/leads", json=payload).status_code == 422


def test_get_lead_includes_owner(client, reps, make_lead):
    lead = make_lead(owner_id=reps["avery"].id)

    response = client.get(f"/api/v1/leads/{lead['id']}")

    assert response.status_code == 200
    assert response.json() == lead
    assert response.json()["owner"] == {"id": reps["avery"].id, "name": "Avery Cole"}


def test_get_unknown_lead_is_404(client):
    response = client.get("/api/v1/leads/42")

    assert response.status_code == 404
    assert response.json() == {"detail": "Lead 42 not found"}


def test_list_leads_is_paged(client, make_lead):
    for _ in range(3):
        make_lead()

    response = client.get("/api/v1/leads", params={"limit": 2, "offset": 1})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert len(body["items"]) == 2


@pytest.mark.parametrize(("limit", "status"), [(0, 422), (1, 200), (200, 200), (201, 422)])
def test_list_leads_limit_bounds(client, limit, status):
    assert client.get("/api/v1/leads", params={"limit": limit}).status_code == status


@pytest.mark.parametrize(
    ("field", "value", "query"),
    [
        ("first_name", "Quinn", "qUI"),
        ("last_name", "Okafor", "OKAF"),
        ("company", "Tailspin Toys", "spin t"),
        ("email", "sam@contoso.example", "CONTOSO.EX"),
    ],
)
def test_search_matches_each_field_case_insensitively(client, make_lead, field, value, query):
    match = make_lead(**{field: value})
    make_lead()

    response = client.get("/api/v1/leads", params={"q": query})

    assert _ids(response) == [match["id"]]
    assert response.json()["total"] == 1


def test_search_treats_wildcards_literally(client, make_lead):
    make_lead()

    assert _ids(client.get("/api/v1/leads", params={"q": "%"})) == []


def test_filter_by_single_status(client, insert_lead):
    working = insert_lead(status="working")
    insert_lead(status="new")

    assert _ids(client.get("/api/v1/leads", params={"status": "working"})) == [working]


def test_filter_by_repeated_status_matches_any(client, insert_lead):
    new = insert_lead(status="new")
    working = insert_lead(status="working")
    insert_lead(status="qualified")

    response = client.get("/api/v1/leads?status=new&status=working")

    assert sorted(_ids(response)) == [new, working]
    assert response.json()["total"] == 2


def test_filter_by_unknown_status_is_422(client):
    assert client.get("/api/v1/leads", params={"status": "lost"}).status_code == 422


def test_filter_by_source(client, make_lead):
    referral = make_lead(source="referral")
    make_lead(source="web")

    assert _ids(client.get("/api/v1/leads", params={"source": "referral"})) == [referral["id"]]


def test_filter_by_unknown_source_is_422(client):
    assert client.get("/api/v1/leads", params={"source": "tv"}).status_code == 422


def test_filter_by_owner(client, reps, make_lead):
    owned = make_lead(owner_id=reps["zoe"].id)
    make_lead(owner_id=reps["avery"].id)
    make_lead()

    assert _ids(client.get("/api/v1/leads", params={"owner_id": reps["zoe"].id})) == [owned["id"]]


# Positions (in insertion order) of the four sortable leads, ascending by each field.
ASCENDING = {
    "created_at": [1, 3, 0, 2],
    "score": [2, 0, 3, 1],
    "company": [2, 3, 0, 1],
    "last_name": [1, 3, 2, 0],
    "status": [3, 1, 2, 0],
}


@pytest.mark.parametrize("field", sorted(ASCENDING))
def test_sort_ascending(client, sortable_leads, field):
    expected = [sortable_leads[i] for i in ASCENDING[field]]

    assert _ids(client.get("/api/v1/leads", params={"sort": field})) == expected


@pytest.mark.parametrize("field", sorted(ASCENDING))
def test_sort_descending(client, sortable_leads, field):
    expected = [sortable_leads[i] for i in reversed(ASCENDING[field])]

    assert _ids(client.get("/api/v1/leads", params={"sort": f"-{field}"})) == expected


def test_default_sort_is_newest_first(client, sortable_leads):
    expected = [sortable_leads[i] for i in reversed(ASCENDING["created_at"])]

    assert _ids(client.get("/api/v1/leads")) == expected


@pytest.mark.parametrize("sort", ["score", "-score", "-created_at"])
def test_ties_are_broken_by_newest_id(client, insert_lead, sort):
    at = datetime(2026, 2, 1)
    first = insert_lead(score=30, created_at=at)
    second = insert_lead(score=30, created_at=at)

    assert _ids(client.get("/api/v1/leads", params={"sort": sort})) == [second, first]


@pytest.mark.parametrize("sort", ["email", "-id", "--score", "", "Score"])
def test_bad_sort_is_400(client, sort):
    response = client.get("/api/v1/leads", params={"sort": sort})

    assert response.status_code == 400
    assert response.json() == {"detail": SORT_MESSAGE}


def test_update_lead_changes_fields(client, reps, make_lead):
    lead = make_lead()

    response = client.patch(
        f"/api/v1/leads/{lead['id']}",
        json={"status": "qualified", "score": 80, "title": "CFO", "owner_id": reps["zoe"].id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "qualified"
    assert body["score"] == 80
    assert body["title"] == "CFO"
    assert body["owner"] == {"id": reps["zoe"].id, "name": "Zoe Park"}
    assert body["company"] == VALID_LEAD["company"]


@pytest.mark.parametrize("status", ["new", "working", "qualified", "disqualified"])
def test_update_lead_accepts_editable_statuses(client, make_lead, status):
    lead = make_lead()

    response = client.patch(f"/api/v1/leads/{lead['id']}", json={"status": status})

    assert response.status_code == 200
    assert response.json()["status"] == status


def test_update_lead_cannot_set_converted(client, make_lead):
    lead = make_lead()

    response = client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "converted"})

    assert response.status_code == 422
    assert client.get(f"/api/v1/leads/{lead['id']}").json()["status"] == "new"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", ""),
        ("last_name", "x" * 81),
        ("email", "nope"),
        ("company", "x" * 201),
        ("score", 101),
        ("score", -1),
        ("source", "tv"),
        ("first_name", None),
        ("email", None),
        ("company", None),
        ("source", None),
        ("status", None),
        ("score", None),
    ],
)
def test_update_lead_rejects_invalid_values(client, make_lead, field, value):
    lead = make_lead()

    assert client.patch(f"/api/v1/leads/{lead['id']}", json={field: value}).status_code == 422


def test_update_lead_can_clear_optional_fields(client, reps, make_lead):
    lead = make_lead(title="CTO", owner_id=reps["zoe"].id)

    response = client.patch(f"/api/v1/leads/{lead['id']}", json={"title": None, "owner_id": None})

    assert response.status_code == 200
    assert response.json()["title"] is None
    assert response.json()["owner"] is None


def test_update_lead_with_unknown_owner_is_404(client, make_lead):
    lead = make_lead()

    response = client.patch(f"/api/v1/leads/{lead['id']}", json={"owner_id": 9})

    assert response.status_code == 404
    assert response.json() == {"detail": "Rep 9 not found"}


def test_update_unknown_lead_is_404(client):
    response = client.patch("/api/v1/leads/42", json={"score": 5})

    assert response.status_code == 404
    assert response.json() == {"detail": "Lead 42 not found"}


def test_converted_lead_cannot_be_edited(client, make_account, insert_lead):
    account = make_account()
    lead_id = insert_lead(status="converted", converted_account_id=account["id"])

    response = client.patch(f"/api/v1/leads/{lead_id}", json={"score": 5})

    assert response.status_code == 409
    assert response.json() == {"detail": "Converted leads cannot be edited"}
    lead = client.get(f"/api/v1/leads/{lead_id}").json()
    assert lead["score"] == 0
    assert lead["converted_account_id"] == account["id"]
