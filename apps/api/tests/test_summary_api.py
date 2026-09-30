from datetime import UTC, datetime, timedelta

import pytest

from app.forecast import parse_quarter


@pytest.fixture
def make_lead(client):
    def make(**overrides):
        payload = {
            "first_name": "Riley",
            "last_name": "Morgan",
            "email": "riley.morgan@fabrikam.example",
            "company": "Fabrikam Logistics",
        }
        payload.update(overrides)
        response = client.post("/api/v1/leads", json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    return make


@pytest.fixture
def account(make_account):
    return make_account()


def test_summary_on_an_empty_database(client):
    response = client.get("/api/v1/summary")

    today = datetime.now(UTC).date()
    quarter, _start, _end = parse_quarter(None, today)

    assert response.status_code == 200
    assert response.json() == {
        "quarter": quarter,
        "leads_by_status": {},
        "open_leads": 0,
        "open_pipeline": "0.00",
        "open_deals": 0,
        "won_this_quarter": "0.00",
    }


def test_summary_figures_on_a_small_data_set(client, account, make_lead, make_opportunity):
    make_lead(email="a@fabrikam.example")
    make_lead(email="b@fabrikam.example")
    lead = make_lead(email="c@fabrikam.example")
    client.patch(f"/api/v1/leads/{lead['id']}", json={"status": "working"})
    disqualified = make_lead(email="d@fabrikam.example")
    client.patch(f"/api/v1/leads/{disqualified['id']}", json={"status": "disqualified"})

    today = datetime.now(UTC).date()
    quarter, start, _end = parse_quarter(None, today)
    outside_quarter = start - timedelta(days=1)

    make_opportunity(account["id"], stage="prospecting", amount="1000.00", close_date="2030-01-01")
    make_opportunity(account["id"], stage="negotiation", amount="2000.00", close_date="2030-01-01")
    make_opportunity(
        account["id"], stage="closed_won", amount="5000.00", close_date=today.isoformat()
    )
    make_opportunity(
        account["id"],
        stage="closed_won",
        amount="9999.00",
        close_date=outside_quarter.isoformat(),
    )
    make_opportunity(account["id"], stage="closed_lost", amount="4000.00", close_date="2030-01-01")

    response = client.get("/api/v1/summary")

    assert response.status_code == 200
    assert response.json() == {
        "quarter": quarter,
        "leads_by_status": {"new": 2, "working": 1, "disqualified": 1},
        "open_leads": 3,
        "open_pipeline": "3000.00",
        "open_deals": 2,
        "won_this_quarter": "5000.00",
    }
