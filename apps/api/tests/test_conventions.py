"""Cross-endpoint conventions: paging, stable order, error statuses, money and PATCH nulls."""

import re

import pytest

MONEY_RE = re.compile(r"^-?\d+\.\d{2}$")
MONEY_FIELDS = {
    "amount",
    "annual_revenue",
    "best_case",
    "commit",
    "open_pipeline",
    "pipeline",
    "quarterly_quota",
    "quota",
    "weighted",
    "won",
    "won_this_quarter",
}


def _make_leads(client, count):
    for i in range(count):
        response = client.post(
            "/api/v1/leads",
            json={
                "first_name": "Sam",
                "last_name": f"Rivera {i % 3}",
                "email": f"sam{i}@lead.example",
                "company": "Same Company",
            },
        )
        assert response.status_code == 201, response.text


def _make_accounts(client, make_account, count):
    for _ in range(count):
        make_account(name="Same Name")


def _make_contacts(client, make_account, make_contact, count):
    account = make_account()
    for i in range(count):
        make_contact(account["id"], email=f"jordan{i}@northwind.example")


def _make_opportunities(client, make_account, make_opportunity, count):
    account = make_account()
    for _ in range(count):
        make_opportunity(account["id"])


LIST_ENDPOINTS = [
    pytest.param("/api/v1/accounts", 25, 200, id="accounts"),
    pytest.param("/api/v1/contacts", 25, 200, id="contacts"),
    pytest.param("/api/v1/leads", 25, 200, id="leads"),
    pytest.param("/api/v1/opportunities", 50, 500, id="opportunities"),
]


@pytest.fixture
def seed(client, make_account, make_contact, make_opportunity):
    def seed_for(path, count):
        if path.endswith("/accounts"):
            _make_accounts(client, make_account, count)
        elif path.endswith("/contacts"):
            _make_contacts(client, make_account, make_contact, count)
        elif path.endswith("/leads"):
            _make_leads(client, count)
        else:
            _make_opportunities(client, make_account, make_opportunity, count)

    return seed_for


@pytest.mark.parametrize(("path", "default_limit", "max_limit"), LIST_ENDPOINTS)
def test_list_endpoint_paging(client, seed, path, default_limit, max_limit):
    count = default_limit + 2
    seed(path, count)

    default = client.get(path)
    assert default.status_code == 200
    body = default.json()
    assert set(body) == {"items", "total"}
    assert len(body["items"]) == default_limit
    assert body["total"] == count

    everything = client.get(path, params={"limit": max_limit}).json()
    assert len(everything["items"]) == count
    assert everything["total"] == count

    assert len(client.get(path, params={"limit": 1}).json()["items"]) == 1
    last_page = client.get(path, params={"offset": count - 1}).json()
    assert len(last_page["items"]) == 1
    assert last_page["total"] == count

    for params in ({"limit": 0}, {"limit": max_limit + 1}, {"offset": -1}):
        response = client.get(path, params=params)
        assert response.status_code == 422, params
        assert response.json()["detail"][0]["loc"] == ["query", next(iter(params))]


@pytest.mark.parametrize(("path", "default_limit", "max_limit"), LIST_ENDPOINTS)
def test_list_endpoint_order_is_stable(client, seed, path, default_limit, max_limit):
    # Every row ties on the sort columns, so only the id tie-breaker decides the order.
    seed(path, 5)

    full = [item["id"] for item in client.get(path).json()["items"]]
    assert full == [item["id"] for item in client.get(path).json()["items"]]
    paged = [
        item["id"]
        for offset in range(5)
        for item in client.get(path, params={"limit": 1, "offset": offset}).json()["items"]
    ]
    assert paged == full
    assert sorted(full) in (full, full[::-1])


def test_reps_stays_a_plain_array(client, reps):
    body = client.get("/api/v1/reps").json()
    assert isinstance(body, list)
    assert [rep["name"] for rep in body] == ["Avery Cole", "Zoe Park"]


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/accounts/1",
        "/api/v1/contacts",
        "/api/v1/leads/1",
        "/api/v1/opportunities/1",
        "/api/v1/reps",
    ],
)
def test_there_are_no_delete_endpoints(client, make_account, path):
    make_account()
    assert client.delete(path).status_code == 405


# Errors


def test_400_for_a_bad_sort(client):
    response = client.get("/api/v1/leads", params={"sort": "nope"})
    assert response.status_code == 400
    assert isinstance(response.json()["detail"], str)


def test_400_for_a_bad_quarter(client):
    response = client.get("/api/v1/forecast", params={"quarter": "2026-Q5"})
    assert response.status_code == 400
    assert response.json() == {"detail": "quarter must look like 2026-Q3"}


@pytest.mark.parametrize(
    ("method", "path", "body", "detail"),
    [
        ("get", "/api/v1/accounts/999", None, "Account 999 not found"),
        ("patch", "/api/v1/accounts/999", {"name": "X"}, "Account 999 not found"),
        ("get", "/api/v1/leads/999", None, "Lead 999 not found"),
        ("patch", "/api/v1/leads/999", {"score": 5}, "Lead 999 not found"),
        ("post", "/api/v1/leads/999/convert", {}, "Lead 999 not found"),
        ("get", "/api/v1/opportunities/999", None, "Opportunity 999 not found"),
        ("patch", "/api/v1/opportunities/999", {"name": "X"}, "Opportunity 999 not found"),
        (
            "post",
            "/api/v1/contacts",
            {"account_id": 999, "first_name": "A", "last_name": "B", "email": "a@b.example"},
            "Account 999 not found",
        ),
        (
            "post",
            "/api/v1/leads",
            {
                "first_name": "A",
                "last_name": "B",
                "email": "a@b.example",
                "company": "C",
                "owner_id": 999,
            },
            "Rep 999 not found",
        ),
    ],
)
def test_404_names_the_model_and_id(client, method, path, body, detail):
    kwargs = {"json": body} if body is not None else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 404
    assert response.json() == {"detail": detail}


def test_409_for_a_converted_lead(client):
    lead = client.post(
        "/api/v1/leads",
        json={"first_name": "A", "last_name": "B", "email": "a@b.example", "company": "C"},
    ).json()
    assert client.post(f"/api/v1/leads/{lead['id']}/convert", json={}).status_code == 200

    for response in (
        client.patch(f"/api/v1/leads/{lead['id']}", json={"score": 10}),
        client.post(f"/api/v1/leads/{lead['id']}/convert", json={}),
    ):
        assert response.status_code == 409
        assert isinstance(response.json()["detail"], str)


@pytest.mark.parametrize(
    ("method", "path", "kwargs", "loc"),
    [
        ("post", "/api/v1/accounts", {"json": {"name": "X"}}, ["body", "industry"]),
        ("get", "/api/v1/accounts", {"params": {"limit": "lots"}}, ["query", "limit"]),
        ("get", "/api/v1/leads/abc", {}, ["path", "lead_id"]),
    ],
)
def test_422_uses_fastapis_validation_shape(client, method, path, kwargs, loc):
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 422
    errors = response.json()["detail"]
    assert isinstance(errors, list)
    assert {"type", "loc", "msg"} <= set(errors[0])
    assert loc in [error["loc"] for error in errors]


# Money


def _money_values(value, path="$"):
    if isinstance(value, dict):
        for key, item in value.items():
            if key in MONEY_FIELDS and not isinstance(item, dict | list):
                yield f"{path}.{key}", key, item
            yield from _money_values(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _money_values(item, f"{path}[{index}]")


def test_every_money_field_is_a_two_place_string(client, reps, make_account, make_contact):
    owner = reps["avery"].id
    account = make_account(annual_revenue="12500000", owner_id=owner)
    make_contact(account["id"])
    for stage, amount in (("closed_won", "1000"), ("negotiation", "2500.5"), ("proposal", "333")):
        response = client.post(
            "/api/v1/opportunities",
            json={
                "account_id": account["id"],
                "name": f"{stage} deal",
                "amount": amount,
                "stage": stage,
                "probability": 33,
                "close_date": "2026-11-15",
                "owner_id": owner,
            },
        )
        assert response.status_code == 201, response.text
    opportunity_id = response.json()["id"]
    lead = client.post(
        "/api/v1/leads",
        json={"first_name": "A", "last_name": "B", "email": "a@b.example", "company": "C"},
    ).json()

    responses = [
        client.get("/api/v1/reps"),
        client.get("/api/v1/accounts"),
        client.get(f"/api/v1/accounts/{account['id']}"),
        client.patch(f"/api/v1/accounts/{account['id']}", json={"annual_revenue": "7.5"}),
        client.get("/api/v1/opportunities"),
        client.get(f"/api/v1/opportunities/{opportunity_id}"),
        client.patch(f"/api/v1/opportunities/{opportunity_id}", json={"amount": "42"}),
        client.post(
            f"/api/v1/leads/{lead['id']}/convert",
            json={"annual_revenue": "10", "opportunity_amount": "99.9"},
        ),
        client.get("/api/v1/forecast", params={"quarter": "2026-Q4"}),
        client.get("/api/v1/summary"),
    ]

    seen = set()
    for response in responses:
        assert response.status_code in (200, 201), response.text
        for where, key, value in _money_values(response.json()):
            seen.add(key)
            assert isinstance(value, str) and MONEY_RE.match(value), (response.url, where, value)
    assert seen == MONEY_FIELDS

    forecast = client.get("/api/v1/forecast", params={"quarter": "2026-Q4"}).json()
    assert isinstance(forecast["by_rep"][0]["attainment_pct"], float)
    assert isinstance(forecast["by_stage"][0]["count"], int)


# Partial updates


@pytest.fixture
def records(client, reps, make_account, make_opportunity):
    owner = reps["zoe"].id
    account = make_account(website="northwind.example", owner_id=owner)
    opportunity = make_opportunity(account["id"], owner_id=owner)
    lead = client.post(
        "/api/v1/leads",
        json={
            "first_name": "A",
            "last_name": "B",
            "email": "a@b.example",
            "company": "C",
            "title": "VP Sales",
            "owner_id": owner,
        },
    ).json()
    return {
        "accounts": account,
        "opportunities": opportunity,
        "leads": lead,
    }


@pytest.mark.parametrize(
    ("resource", "optional", "required"),
    [
        ("accounts", "website", "name"),
        ("accounts", "owner_id", "region"),
        ("leads", "title", "first_name"),
        ("leads", "owner_id", "email"),
        ("opportunities", "owner_id", "name"),
        ("opportunities", "owner_id", "probability"),
    ],
)
def test_patch_null_clears_optional_and_rejects_required(
    client, records, resource, optional, required
):
    path = f"/api/v1/{resource}/{records[resource]['id']}"
    record = client.get(path).json()
    assert record[optional] is not None

    cleared = client.patch(path, json={optional: None})
    assert cleared.status_code == 200, cleared.text
    body = cleared.json()
    assert body[optional] is None
    untouched = {
        key: value
        for key, value in record.items()
        if key in body and key not in (optional, "owner")
    }
    assert {key: body[key] for key in untouched} == untouched

    rejected = client.patch(path, json={required: None})
    assert rejected.status_code == 422
    assert isinstance(rejected.json()["detail"], list)
    assert client.get(path).json()[required] == record[required]
