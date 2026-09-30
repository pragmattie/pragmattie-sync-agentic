from datetime import UTC, date, datetime, timedelta

import pytest


@pytest.fixture
def account(make_account):
    return make_account()


def test_forecast_defaults_to_the_current_quarter(client, reps):
    today = datetime.now(UTC).date()
    q = (today.month - 1) // 3 + 1
    expected_quarter = f"{today.year}-Q{q}"

    response = client.get("/api/v1/forecast")

    assert response.status_code == 200
    body = response.json()
    assert body["quarter"] == expected_quarter
    assert body["quota"] == "550000.00"
    assert body["won"] == "0.00"
    assert body["by_rep"] == [
        {
            "rep": {"id": reps["avery"].id, "name": "Avery Cole"},
            "quota": "300000.00",
            "won": "0.00",
            "commit": "0.00",
            "weighted": "0.00",
            "attainment_pct": 0.0,
        },
        {
            "rep": {"id": reps["zoe"].id, "name": "Zoe Park"},
            "quota": "250000.00",
            "won": "0.00",
            "commit": "0.00",
            "weighted": "0.00",
            "attainment_pct": 0.0,
        },
    ]
    assert len(body["by_month"]) == 3
    assert [row["stage"] for row in body["by_stage"]] == [
        "prospecting",
        "qualification",
        "proposal",
        "negotiation",
    ]


def test_forecast_with_an_explicit_quarter(client, account, reps, make_opportunity):
    make_opportunity(
        account["id"],
        stage="closed_won",
        amount="10000.00",
        close_date="2026-08-01",
        owner_id=reps["zoe"].id,
    )
    make_opportunity(
        account["id"],
        stage="closed_won",
        amount="99999.00",
        close_date="2026-01-01",
        owner_id=reps["zoe"].id,
    )

    response = client.get("/api/v1/forecast", params={"quarter": "2026-Q3"})

    assert response.status_code == 200
    body = response.json()
    assert body == {
        "quarter": "2026-Q3",
        "start": "2026-07-01",
        "end": "2026-09-30",
        "quota": "550000.00",
        "won": "10000.00",
        "commit": "10000.00",
        "best_case": "10000.00",
        "pipeline": "0.00",
        "weighted": "10000.00",
        "by_month": [
            {
                "month": "2026-07",
                "won": "0.00",
                "commit": "0.00",
                "best_case": "0.00",
                "weighted": "0.00",
            },
            {
                "month": "2026-08",
                "won": "10000.00",
                "commit": "10000.00",
                "best_case": "10000.00",
                "weighted": "10000.00",
            },
            {
                "month": "2026-09",
                "won": "0.00",
                "commit": "0.00",
                "best_case": "0.00",
                "weighted": "0.00",
            },
        ],
        "by_rep": [
            {
                "rep": {"id": reps["zoe"].id, "name": "Zoe Park"},
                "quota": "250000.00",
                "won": "10000.00",
                "commit": "10000.00",
                "weighted": "10000.00",
                "attainment_pct": 4.0,
            },
            {
                "rep": {"id": reps["avery"].id, "name": "Avery Cole"},
                "quota": "300000.00",
                "won": "0.00",
                "commit": "0.00",
                "weighted": "0.00",
                "attainment_pct": 0.0,
            },
        ],
        "by_stage": [
            {"stage": "prospecting", "count": 0, "amount": "0.00"},
            {"stage": "qualification", "count": 0, "amount": "0.00"},
            {"stage": "proposal", "count": 0, "amount": "0.00"},
            {"stage": "negotiation", "count": 0, "amount": "0.00"},
        ],
    }


def test_forecast_rejects_a_badly_shaped_quarter(client):
    response = client.get("/api/v1/forecast", params={"quarter": "2026-Q9"})

    assert response.status_code == 400
    assert response.json() == {"detail": "quarter must look like 2026-Q3"}


def test_forecast_only_counts_deals_closing_in_the_quarter(client, account, reps, make_opportunity):
    make_opportunity(
        account["id"],
        stage="closed_won",
        amount="500.00",
        close_date=(date(2026, 9, 30) + timedelta(days=1)).isoformat(),
        owner_id=reps["zoe"].id,
    )

    body = client.get("/api/v1/forecast", params={"quarter": "2026-Q3"}).json()

    assert body["won"] == "0.00"
