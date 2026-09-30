from datetime import date, timedelta

import pytest
from sqlalchemy.orm import Session

from app.models import Lead

VALID_LEAD = {
    "first_name": "Riley",
    "last_name": "Morgan",
    "email": "riley.morgan@fabrikam.example",
    "company": "Fabrikam Logistics",
}


@pytest.fixture
def make_lead(client):
    def make(**overrides):
        response = client.post("/api/v1/leads", json={**VALID_LEAD, **overrides})
        assert response.status_code == 201, response.text
        return response.json()

    return make


@pytest.fixture
def insert_lead(sqlite_engine):
    """Insert a lead directly, for states the API cannot create (disqualified, converted)."""

    def insert(**overrides):
        values = {**VALID_LEAD, "source": "web", **overrides}
        with Session(sqlite_engine, expire_on_commit=False) as session:
            lead = Lead(**values)
            session.add(lead)
            session.commit()
            return lead.id

    return insert


def test_convert_with_a_deal_creates_account_contact_and_opportunity(client, reps, make_lead):
    lead = make_lead(
        title="VP Ops",
        owner_id=reps["zoe"].id,
    )

    response = client.post(
        f"/api/v1/leads/{lead['id']}/convert",
        json={
            "industry": "Logistics",
            "employee_count": 120,
            "annual_revenue": "5000000.00",
            "region": "East",
            "opportunity_name": "Fabrikam - Platform rollout",
            "opportunity_amount": "75000.00",
            "opportunity_close_date": "2026-11-01",
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["account_id"] is not None
    assert body["contact_id"] is not None
    assert body["opportunity_id"] is not None
    assert body["lead"]["status"] == "converted"
    assert body["lead"]["converted_account_id"] == body["account_id"]

    account = client.get(f"/api/v1/accounts/{body['account_id']}").json()
    assert account["name"] == "Fabrikam Logistics"
    assert account["industry"] == "Logistics"
    assert account["employee_count"] == 120
    assert account["annual_revenue"] == "5000000.00"
    assert account["region"] == "East"
    assert account["owner_id"] == reps["zoe"].id

    contact = next(c for c in account["contacts"] if c["id"] == body["contact_id"])
    assert contact["first_name"] == "Riley"
    assert contact["last_name"] == "Morgan"
    assert contact["email"] == "riley.morgan@fabrikam.example"
    assert contact["title"] == "VP Ops"

    opportunity = next(o for o in account["opportunities"] if o["id"] == body["opportunity_id"])
    assert opportunity["name"] == "Fabrikam - Platform rollout"
    assert opportunity["amount"] == "75000.00"
    assert opportunity["stage"] == "qualification"
    assert opportunity["probability"] == 25
    assert opportunity["close_date"] == "2026-11-01"
    assert opportunity["owner_id"] == reps["zoe"].id


def test_convert_without_a_deal_creates_no_opportunity(client, make_lead):
    lead = make_lead()

    response = client.post(f"/api/v1/leads/{lead['id']}/convert", json={})

    assert response.status_code == 200
    body = response.json()
    assert body["opportunity_id"] is None

    account = client.get(f"/api/v1/accounts/{body['account_id']}").json()
    assert account["opportunities"] == []


def test_convert_applies_default_industry(client, make_lead):
    lead = make_lead()

    response = client.post(f"/api/v1/leads/{lead['id']}/convert", json={})

    account = client.get(f"/api/v1/accounts/{response.json()['account_id']}").json()
    assert account["industry"] == "Unknown"


def test_convert_applies_default_employee_count(client, make_lead):
    lead = make_lead()

    response = client.post(f"/api/v1/leads/{lead['id']}/convert", json={})

    account = client.get(f"/api/v1/accounts/{response.json()['account_id']}").json()
    assert account["employee_count"] == 50


def test_convert_applies_default_annual_revenue(client, make_lead):
    lead = make_lead()

    response = client.post(f"/api/v1/leads/{lead['id']}/convert", json={})

    account = client.get(f"/api/v1/accounts/{response.json()['account_id']}").json()
    assert account["annual_revenue"] == "0.00"


def test_convert_applies_default_region(client, make_lead):
    lead = make_lead()

    response = client.post(f"/api/v1/leads/{lead['id']}/convert", json={})

    account = client.get(f"/api/v1/accounts/{response.json()['account_id']}").json()
    assert account["region"] == "North America"


def test_convert_applies_default_opportunity_name(client, make_lead):
    lead = make_lead(company="Tailspin Toys")

    response = client.post(
        f"/api/v1/leads/{lead['id']}/convert", json={"opportunity_amount": "1000.00"}
    )

    account = client.get(f"/api/v1/accounts/{response.json()['account_id']}").json()
    opportunity = account["opportunities"][0]
    assert opportunity["name"] == "Tailspin Toys - New business"


def test_convert_applies_default_opportunity_close_date(client, make_lead):
    lead = make_lead()

    response = client.post(
        f"/api/v1/leads/{lead['id']}/convert", json={"opportunity_amount": "1000.00"}
    )

    account = client.get(f"/api/v1/accounts/{response.json()['account_id']}").json()
    opportunity = account["opportunities"][0]
    assert opportunity["close_date"] == str(date.today() + timedelta(days=60))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("employee_count", 0),
        ("annual_revenue", "-1.00"),
        ("opportunity_amount", "0.00"),
        ("opportunity_amount", "-5.00"),
        ("industry", ""),
        ("region", ""),
    ],
)
def test_convert_rejects_invalid_values(client, make_lead, field, value):
    lead = make_lead()

    response = client.post(f"/api/v1/leads/{lead['id']}/convert", json={field: value})

    assert response.status_code == 422


def test_convert_already_converted_lead_is_409(client, make_account, insert_lead):
    account = make_account()
    lead_id = insert_lead(status="converted", converted_account_id=account["id"])

    response = client.post(f"/api/v1/leads/{lead_id}/convert", json={})

    assert response.status_code == 409
    assert response.json() == {"detail": "Lead is already converted"}


def test_convert_disqualified_lead_is_409(client, insert_lead):
    lead_id = insert_lead(status="disqualified")

    response = client.post(f"/api/v1/leads/{lead_id}/convert", json={})

    assert response.status_code == 409
    assert response.json() == {"detail": "Disqualified leads cannot be converted"}


@pytest.mark.parametrize("status", ["new", "working", "qualified"])
def test_convert_allows_other_statuses(client, insert_lead, status):
    lead_id = insert_lead(status=status)

    response = client.post(f"/api/v1/leads/{lead_id}/convert", json={})

    assert response.status_code == 200


def test_convert_unknown_lead_is_404(client):
    response = client.post("/api/v1/leads/42/convert", json={})

    assert response.status_code == 404
    assert response.json() == {"detail": "Lead 42 not found"}


def test_failed_conversion_leaves_no_account_or_contact(client, make_lead, monkeypatch):
    lead = make_lead()

    def failing_commit(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(Session, "commit", failing_commit)

    with pytest.raises(RuntimeError):
        client.post(f"/api/v1/leads/{lead['id']}/convert", json={})

    monkeypatch.undo()

    assert client.get("/api/v1/accounts").json()["total"] == 0
    assert client.get(f"/api/v1/leads/{lead['id']}").json()["status"] == "new"


def test_converted_lead_then_refuses_edits(client, make_lead):
    lead = make_lead()
    convert_response = client.post(f"/api/v1/leads/{lead['id']}/convert", json={})
    assert convert_response.status_code == 200

    response = client.patch(f"/api/v1/leads/{lead['id']}", json={"score": 5})

    assert response.status_code == 409
    assert response.json() == {"detail": "Converted leads cannot be edited"}
