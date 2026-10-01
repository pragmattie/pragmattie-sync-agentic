"""One deal's life through the API: lead, conversion, every open stage, then won.

Reps come from the seed data rather than the API, so the `reps` fixture inserts them;
every other step goes through HTTP.
"""

from datetime import UTC, datetime

API = "/api/v1"


def test_a_reps_lead_becomes_a_won_deal_counted_everywhere(client, reps):
    rep = reps["zoe"]
    today = datetime.now(UTC).date()

    # 1. A rep's lead.
    response = client.post(
        f"{API}/leads",
        json={
            "first_name": "Riley",
            "last_name": "Morgan",
            "email": "riley.morgan@fabrikam.example",
            "company": "Fabrikam Logistics",
            "title": "VP of Operations",
            "source": "referral",
            "owner_id": rep.id,
        },
    )
    assert response.status_code == 201, response.text
    lead = response.json()
    assert lead["status"] == "new"

    # 2. Converted with a deal closing this quarter (today is always in it).
    response = client.post(
        f"{API}/leads/{lead['id']}/convert",
        json={"opportunity_amount": "48000.00", "opportunity_close_date": today.isoformat()},
    )
    assert response.status_code == 200, response.text
    converted = response.json()
    account_id, deal_id = converted["account_id"], converted["opportunity_id"]
    assert converted["lead"]["status"] == "converted"
    assert deal_id is not None

    deal = client.get(f"{API}/opportunities/{deal_id}").json()
    assert deal["stage"] == "qualification"
    assert deal["probability"] == 25
    assert deal["owner_id"] == rep.id
    assert client.get(f"{API}/accounts/{account_id}").json()["open_pipeline"] == "48000.00"

    # 3. Through every open stage in order, the probability following the stage. Conversion
    # lands the deal in qualification, so it goes back to prospecting first.
    for stage, probability in [
        ("prospecting", 10),
        ("qualification", 25),
        ("proposal", 50),
        ("negotiation", 75),
    ]:
        response = client.patch(f"{API}/opportunities/{deal_id}", json={"stage": stage})
        assert response.status_code == 200, response.text
        assert response.json()["stage"] == stage
        assert response.json()["probability"] == probability
        assert client.get(f"{API}/accounts/{account_id}").json()["open_pipeline"] == "48000.00"

    # 4. Won.
    response = client.patch(f"{API}/opportunities/{deal_id}", json={"stage": "closed_won"})
    assert response.status_code == 200, response.text
    assert response.json()["probability"] == 100

    # 5. The account shows the deal and no open pipeline.
    account = client.get(f"{API}/accounts/{account_id}").json()
    assert account["name"] == "Fabrikam Logistics"
    assert account["owner_id"] == rep.id
    assert [(o["id"], o["stage"]) for o in account["opportunities"]] == [(deal_id, "closed_won")]
    assert account["open_pipeline"] == "0.00"

    summary = client.get(f"{API}/summary").json()
    assert summary["won_this_quarter"] == "48000.00"
    assert summary["open_deals"] == 0
    assert summary["open_pipeline"] == "0.00"
    assert summary["leads_by_status"] == {"converted": 1}
    assert summary["open_leads"] == 0

    forecast = client.get(f"{API}/forecast").json()
    assert forecast["won"] == "48000.00"
    rows = {row["rep"]["id"]: row for row in forecast["by_rep"]}
    assert rows[rep.id]["won"] == "48000.00"
    assert rows[rep.id]["attainment_pct"] == 19.2
    assert rows[reps["avery"].id]["won"] == "0.00"
