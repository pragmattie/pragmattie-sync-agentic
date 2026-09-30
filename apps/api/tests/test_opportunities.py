from datetime import datetime

import pytest

from app.stages import OPEN_STAGES, STAGE_PROBABILITY


@pytest.fixture
def account(make_account):
    return make_account()


def _payload(account_id, **overrides):
    return {
        "account_id": account_id,
        "name": "Northwind renewal",
        "amount": "50000.00",
        "close_date": "2026-12-15",
        **overrides,
    }


def _ids(response):
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_stage_table_is_in_order_with_its_defaults():
    assert STAGE_PROBABILITY == {
        "prospecting": 10,
        "qualification": 25,
        "proposal": 50,
        "negotiation": 75,
        "closed_won": 100,
        "closed_lost": 0,
    }
    assert list(STAGE_PROBABILITY)[:4] == list(OPEN_STAGES)


def test_create_opportunity_returns_the_full_shape(client, reps, account):
    owner = reps["zoe"]
    response = client.post(
        "/api/v1/opportunities",
        json=_payload(account["id"], stage="proposal", owner_id=owner.id),
    )

    assert response.status_code == 201
    body = response.json()
    datetime.fromisoformat(body.pop("created_at"))
    assert isinstance(body.pop("id"), int)
    assert body == {
        "account_id": account["id"],
        "account": {"id": account["id"], "name": "Northwind Traders"},
        "name": "Northwind renewal",
        "amount": "50000.00",
        "stage": "proposal",
        "probability": 50,
        "close_date": "2026-12-15",
        "owner_id": owner.id,
        "owner": {"id": owner.id, "name": "Zoe Park"},
    }


def test_create_opportunity_defaults_to_prospecting(client, account):
    body = client.post("/api/v1/opportunities", json=_payload(account["id"])).json()

    assert body["stage"] == "prospecting"
    assert body["probability"] == 10
    assert body["owner_id"] is None
    assert body["owner"] is None


@pytest.mark.parametrize(("stage", "probability"), list(STAGE_PROBABILITY.items()))
def test_create_opportunity_uses_the_stage_default_probability(client, account, stage, probability):
    body = client.post("/api/v1/opportunities", json=_payload(account["id"], stage=stage)).json()

    assert body["probability"] == probability


@pytest.mark.parametrize("probability", [0, 35, 100])
def test_create_opportunity_keeps_an_explicit_probability(client, account, probability):
    body = client.post(
        "/api/v1/opportunities",
        json=_payload(account["id"], stage="proposal", probability=probability),
    ).json()

    assert body["probability"] == probability


def test_create_opportunity_with_unknown_account_is_404(client):
    response = client.post("/api/v1/opportunities", json=_payload(9))

    assert response.status_code == 404
    assert response.json() == {"detail": "Account 9 not found"}


def test_create_opportunity_with_unknown_owner_is_404(client, account):
    response = client.post("/api/v1/opportunities", json=_payload(account["id"], owner_id=9))

    assert response.status_code == 404
    assert response.json() == {"detail": "Rep 9 not found"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "x" * 201),
        ("amount", "0"),
        ("amount", "-1.00"),
        ("amount", "1.234"),
        ("amount", "10000000000.00"),
        ("probability", -1),
        ("probability", 101),
        ("stage", "won"),
        ("close_date", "not-a-date"),
    ],
)
def test_create_opportunity_rejects_values_outside_the_limits(client, account, field, value):
    response = client.post("/api/v1/opportunities", json=_payload(account["id"], **{field: value}))

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [("name", "x"), ("name", "x" * 200), ("amount", "0.01"), ("amount", "9999999999.99")],
)
def test_create_opportunity_accepts_values_at_the_limits(client, account, field, value):
    response = client.post("/api/v1/opportunities", json=_payload(account["id"], **{field: value}))

    assert response.status_code == 201, response.text


@pytest.mark.parametrize("field", ["account_id", "name", "amount", "close_date"])
def test_create_opportunity_requires_fields(client, account, field):
    payload = {key: value for key, value in _payload(account["id"]).items() if key != field}

    assert client.post("/api/v1/opportunities", json=payload).status_code == 422


def test_list_opportunities_orders_by_close_date_then_id(client, account, make_opportunity):
    later = make_opportunity(account["id"], close_date="2027-02-01")
    first_tie = make_opportunity(account["id"], close_date="2026-11-01")
    second_tie = make_opportunity(account["id"], close_date="2026-11-01")
    earliest = make_opportunity(account["id"], close_date="2026-10-15")

    response = client.get("/api/v1/opportunities")

    assert _ids(response) == [earliest["id"], first_tie["id"], second_tie["id"], later["id"]]
    assert response.json()["total"] == 4


def test_list_opportunities_filters_by_repeated_stage(client, account, make_opportunity):
    proposal = make_opportunity(account["id"], stage="proposal", close_date="2026-11-01")
    make_opportunity(account["id"], stage="prospecting", close_date="2026-11-02")
    won = make_opportunity(account["id"], stage="closed_won", close_date="2026-11-03")

    response = client.get(
        "/api/v1/opportunities", params=[("stage", "proposal"), ("stage", "closed_won")]
    )

    assert _ids(response) == [proposal["id"], won["id"]]
    assert response.json()["total"] == 2


def test_list_opportunities_filters_by_a_single_stage(client, account, make_opportunity):
    make_opportunity(account["id"], stage="proposal")
    lost = make_opportunity(account["id"], stage="closed_lost")

    assert _ids(client.get("/api/v1/opportunities", params={"stage": "closed_lost"})) == [
        lost["id"]
    ]


def test_list_opportunities_rejects_an_unknown_stage(client):
    assert client.get("/api/v1/opportunities", params={"stage": "won"}).status_code == 422


def test_list_opportunities_filters_by_owner(client, reps, account, make_opportunity):
    make_opportunity(account["id"], owner_id=reps["zoe"].id)
    avery = make_opportunity(account["id"], owner_id=reps["avery"].id)
    make_opportunity(account["id"])

    response = client.get("/api/v1/opportunities", params={"owner_id": reps["avery"].id})

    assert _ids(response) == [avery["id"]]


def test_list_opportunities_filters_by_account(client, make_account, make_opportunity):
    northwind = make_account()
    acme = make_account(name="Acme Logistics")
    make_opportunity(northwind["id"])
    deal = make_opportunity(acme["id"])

    response = client.get("/api/v1/opportunities", params={"account_id": acme["id"]})

    assert _ids(response) == [deal["id"]]
    assert response.json()["items"][0]["account"] == {"id": acme["id"], "name": "Acme Logistics"}


def test_list_opportunities_close_date_bounds_are_inclusive(client, account, make_opportunity):
    dates = ["2026-10-31", "2026-11-01", "2026-11-15", "2026-11-30", "2026-12-01"]
    deals = [make_opportunity(account["id"], close_date=day) for day in dates]

    response = client.get(
        "/api/v1/opportunities", params={"close_from": "2026-11-01", "close_to": "2026-11-30"}
    )

    assert _ids(response) == [deal["id"] for deal in deals[1:4]]


@pytest.mark.parametrize(
    ("params", "expected"),
    [({"close_from": "2026-11-15"}, [2, 3, 4]), ({"close_to": "2026-11-15"}, [0, 1, 2])],
)
def test_list_opportunities_close_date_bounds_work_alone(
    client, account, make_opportunity, params, expected
):
    dates = ["2026-10-31", "2026-11-01", "2026-11-15", "2026-11-30", "2026-12-01"]
    deals = [make_opportunity(account["id"], close_date=day) for day in dates]

    response = client.get("/api/v1/opportunities", params=params)

    assert _ids(response) == [deals[index]["id"] for index in expected]


def test_list_opportunities_filters_by_name_case_insensitively(client, account, make_opportunity):
    renewal = make_opportunity(account["id"], name="Northwind Renewal 2027")
    make_opportunity(account["id"], name="Acme expansion")

    assert _ids(client.get("/api/v1/opportunities", params={"q": "rEnEwAl"})) == [renewal["id"]]


def test_list_opportunities_name_filter_treats_wildcards_literally(
    client, account, make_opportunity
):
    discount = make_opportunity(account["id"], name="10% uplift")
    make_opportunity(account["id"], name="Acme expansion")

    assert _ids(client.get("/api/v1/opportunities", params={"q": "%"})) == [discount["id"]]


def test_list_opportunities_limit_defaults_to_50(client, account, make_opportunity):
    for _ in range(51):
        make_opportunity(account["id"])

    body = client.get("/api/v1/opportunities").json()

    assert body["total"] == 51
    assert len(body["items"]) == 50


def test_list_opportunities_pages_after_counting_every_match(client, account, make_opportunity):
    deals = [make_opportunity(account["id"], close_date=f"2026-11-0{day}") for day in range(1, 6)]

    response = client.get("/api/v1/opportunities", params={"limit": 2, "offset": 1})

    assert _ids(response) == [deals[1]["id"], deals[2]["id"]]
    assert response.json()["total"] == 5


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 501}, {"offset": -1}], ids=["limit0", "limit501", "neg"]
)
def test_list_opportunities_rejects_paging_outside_the_bounds(client, params):
    assert client.get("/api/v1/opportunities", params=params).status_code == 422


@pytest.mark.parametrize("limit", [1, 500])
def test_list_opportunities_accepts_limit_bounds(client, limit):
    assert client.get("/api/v1/opportunities", params={"limit": limit}).status_code == 200


def test_get_opportunity(client, account, make_opportunity):
    deal = make_opportunity(account["id"])

    response = client.get(f"/api/v1/opportunities/{deal['id']}")

    assert response.status_code == 200
    assert response.json() == deal


def test_get_unknown_opportunity_is_404(client):
    response = client.get("/api/v1/opportunities/7")

    assert response.status_code == 404
    assert response.json() == {"detail": "Opportunity 7 not found"}


@pytest.mark.parametrize(("stage", "probability"), list(STAGE_PROBABILITY.items())[1:])
def test_patch_stage_resets_probability_to_the_new_default(
    client, account, make_opportunity, stage, probability
):
    deal = make_opportunity(account["id"], probability=40)

    body = client.patch(f"/api/v1/opportunities/{deal['id']}", json={"stage": stage}).json()

    assert body["stage"] == stage
    assert body["probability"] == probability


def test_patch_stage_with_explicit_probability_keeps_it(client, account, make_opportunity):
    deal = make_opportunity(account["id"])

    body = client.patch(
        f"/api/v1/opportunities/{deal['id']}", json={"stage": "negotiation", "probability": 60}
    ).json()

    assert body["stage"] == "negotiation"
    assert body["probability"] == 60


def test_patch_same_stage_leaves_probability_alone(client, account, make_opportunity):
    deal = make_opportunity(account["id"], stage="proposal", probability=65)

    body = client.patch(f"/api/v1/opportunities/{deal['id']}", json={"stage": "proposal"}).json()

    assert body["probability"] == 65


def test_patch_other_fields_leaves_probability_alone(client, reps, account, make_opportunity):
    deal = make_opportunity(account["id"], stage="proposal", probability=65)

    response = client.patch(
        f"/api/v1/opportunities/{deal['id']}",
        json={
            "name": "Northwind expansion",
            "amount": "72500.50",
            "close_date": "2027-01-31",
            "owner_id": reps["avery"].id,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Northwind expansion"
    assert body["amount"] == "72500.50"
    assert body["close_date"] == "2027-01-31"
    assert body["owner"] == {"id": reps["avery"].id, "name": "Avery Cole"}
    assert body["stage"] == "proposal"
    assert body["probability"] == 65
    assert client.get(f"/api/v1/opportunities/{deal['id']}").json() == body


def test_patch_probability_alone(client, account, make_opportunity):
    deal = make_opportunity(account["id"])

    body = client.patch(f"/api/v1/opportunities/{deal['id']}", json={"probability": 15}).json()

    assert body["stage"] == "prospecting"
    assert body["probability"] == 15


def test_patch_can_move_the_deal_to_another_account(client, make_account, make_opportunity):
    deal = make_opportunity(make_account()["id"])
    acme = make_account(name="Acme Logistics")

    body = client.patch(
        f"/api/v1/opportunities/{deal['id']}", json={"account_id": acme["id"]}
    ).json()

    assert body["account"] == {"id": acme["id"], "name": "Acme Logistics"}


def test_patch_with_null_owner_clears_it(client, reps, account, make_opportunity):
    deal = make_opportunity(account["id"], owner_id=reps["zoe"].id)

    body = client.patch(f"/api/v1/opportunities/{deal['id']}", json={"owner_id": None}).json()

    assert body["owner_id"] is None
    assert body["owner"] is None


def test_patch_unknown_opportunity_is_404(client):
    response = client.patch("/api/v1/opportunities/7", json={"name": "Anything"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Opportunity 7 not found"}


@pytest.mark.parametrize(
    ("field", "value", "detail"),
    [("account_id", 9, "Account 9 not found"), ("owner_id", 9, "Rep 9 not found")],
)
def test_patch_with_unknown_reference_is_404(
    client, account, make_opportunity, field, value, detail
):
    deal = make_opportunity(account["id"])

    response = client.patch(f"/api/v1/opportunities/{deal['id']}", json={field: value})

    assert response.status_code == 404
    assert response.json() == {"detail": detail}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "x" * 201),
        ("amount", "0"),
        ("probability", 101),
        ("probability", -1),
        ("stage", "won"),
        ("name", None),
        ("amount", None),
        ("stage", None),
        ("probability", None),
        ("close_date", None),
        ("account_id", None),
    ],
)
def test_patch_validates_like_create(client, account, make_opportunity, field, value):
    deal = make_opportunity(account["id"])

    response = client.patch(f"/api/v1/opportunities/{deal['id']}", json={field: value})

    assert response.status_code == 422
