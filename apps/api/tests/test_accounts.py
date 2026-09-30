from datetime import datetime

import pytest

VALID_ACCOUNT = {
    "name": "Northwind Traders",
    "industry": "Retail",
    "employee_count": 250,
    "annual_revenue": "12500000.00",
    "region": "West",
}


def test_create_account_returns_the_full_shape(client, reps):
    owner = reps["zoe"]
    response = client.post(
        "/api/v1/accounts",
        json={**VALID_ACCOUNT, "website": "https://northwind.example", "owner_id": owner.id},
    )

    assert response.status_code == 201
    body = response.json()
    datetime.fromisoformat(body.pop("created_at"))
    assert isinstance(body.pop("id"), int)
    assert body == {
        **VALID_ACCOUNT,
        "website": "https://northwind.example",
        "owner_id": owner.id,
        "owner": {"id": owner.id, "name": "Zoe Park"},
        "open_pipeline": "0.00",
        "contact_count": 0,
    }


def test_create_account_without_optional_fields(make_account):
    account = make_account()

    assert account["website"] is None
    assert account["owner_id"] is None
    assert account["owner"] is None


def test_create_account_with_unknown_owner_is_404(client):
    response = client.post("/api/v1/accounts", json={**VALID_ACCOUNT, "owner_id": 9})

    assert response.status_code == 404
    assert response.json() == {"detail": "Rep 9 not found"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", "x" * 201),
        ("industry", ""),
        ("industry", "x" * 81),
        ("employee_count", 0),
        ("employee_count", 2_147_483_648),
        ("annual_revenue", "-0.01"),
        ("annual_revenue", "1.234"),
        ("annual_revenue", "1000000000000.00"),
        ("region", ""),
        ("region", "x" * 51),
        ("website", "x" * 201),
    ],
)
def test_create_account_rejects_values_outside_the_limits(client, field, value):
    response = client.post("/api/v1/accounts", json={**VALID_ACCOUNT, field: value})

    assert response.status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", "x" * 200),
        ("industry", "x" * 80),
        ("employee_count", 1),
        ("annual_revenue", "0"),
        ("annual_revenue", "999999999999.99"),
        ("region", "x" * 50),
        ("website", "x" * 200),
    ],
)
def test_create_account_accepts_values_at_the_limits(client, field, value):
    response = client.post("/api/v1/accounts", json={**VALID_ACCOUNT, field: value})

    assert response.status_code == 201


REQUIRED_FIELDS = ["name", "industry", "employee_count", "annual_revenue", "region"]


@pytest.mark.parametrize("field", REQUIRED_FIELDS)
def test_create_account_requires_fields(client, field):
    payload = {key: value for key, value in VALID_ACCOUNT.items() if key != field}

    assert client.post("/api/v1/accounts", json=payload).status_code == 422


def test_list_accounts_orders_by_name_and_reports_contact_counts(
    client, make_account, make_contact
):
    make_account(name="Umbrella Foods")
    acme = make_account(name="Acme Logistics")
    make_contact(acme["id"])
    make_contact(acme["id"], first_name="Sam", email="sam@acme.example")

    response = client.get("/api/v1/accounts")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert [item["name"] for item in body["items"]] == ["Acme Logistics", "Umbrella Foods"]
    assert [item["contact_count"] for item in body["items"]] == [2, 0]
    assert all(item["open_pipeline"] == "0.00" for item in body["items"])


def test_list_accounts_filters_by_name_case_insensitively(client, make_account):
    make_account(name="Acme Logistics")
    make_account(name="Northwind Traders")

    body = client.get("/api/v1/accounts", params={"q": "aCmE"}).json()

    assert body["total"] == 1
    assert [item["name"] for item in body["items"]] == ["Acme Logistics"]


def test_list_accounts_name_filter_treats_wildcards_literally(client, make_account):
    make_account(name="Acme Logistics")
    make_account(name="100% Solar")

    body = client.get("/api/v1/accounts", params={"q": "%"}).json()

    assert [item["name"] for item in body["items"]] == ["100% Solar"]


def test_list_accounts_filters_by_industry_exactly(client, make_account):
    make_account(name="Acme Logistics", industry="Logistics")
    make_account(name="Globex Logistics Software", industry="Logistics Software")

    body = client.get("/api/v1/accounts", params={"industry": "Logistics"}).json()

    assert [item["name"] for item in body["items"]] == ["Acme Logistics"]


def test_list_accounts_filters_by_owner(client, reps, make_account):
    make_account(name="Acme Logistics", owner_id=reps["zoe"].id)
    make_account(name="Globex", owner_id=reps["avery"].id)
    make_account(name="Initech")

    body = client.get("/api/v1/accounts", params={"owner_id": reps["avery"].id}).json()

    assert [item["name"] for item in body["items"]] == ["Globex"]
    assert body["items"][0]["owner"] == {"id": reps["avery"].id, "name": "Avery Cole"}


def test_list_accounts_pages_after_counting_every_match(client, make_account):
    for name in ["Delta", "Alpha", "Echo", "Charlie", "Bravo"]:
        make_account(name=name)

    body = client.get("/api/v1/accounts", params={"limit": 2, "offset": 1}).json()

    assert body["total"] == 5
    assert [item["name"] for item in body["items"]] == ["Bravo", "Charlie"]


def test_list_accounts_limit_defaults_to_25(client, make_account):
    for index in range(26):
        make_account(name=f"Account {index:02d}")

    body = client.get("/api/v1/accounts").json()

    assert body["total"] == 26
    assert len(body["items"]) == 25


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"offset": -1}], ids=["limit0", "limit201", "neg"]
)
def test_list_accounts_rejects_paging_outside_the_bounds(client, params):
    assert client.get("/api/v1/accounts", params=params).status_code == 422


@pytest.mark.parametrize("limit", [1, 200])
def test_list_accounts_accepts_limit_bounds(client, limit):
    assert client.get("/api/v1/accounts", params={"limit": limit}).status_code == 200


def test_get_account_includes_contacts_and_opportunities(client, make_account, make_contact):
    account = make_account()
    make_contact(account["id"], last_name="Young", email="y@northwind.example")
    make_contact(account["id"], last_name="Adams", email="a@northwind.example")

    response = client.get(f"/api/v1/accounts/{account['id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "Northwind Traders"
    assert body["contact_count"] == 2
    assert body["open_pipeline"] == "0.00"
    assert [contact["last_name"] for contact in body["contacts"]] == ["Adams", "Young"]
    assert body["opportunities"] == []


def test_get_unknown_account_is_404(client):
    response = client.get("/api/v1/accounts/7")

    assert response.status_code == 404
    assert response.json() == {"detail": "Account 7 not found"}


def test_patch_account_changes_only_the_fields_sent(client, reps, make_account):
    account = make_account(website="https://northwind.example", owner_id=reps["zoe"].id)

    response = client.patch(
        f"/api/v1/accounts/{account['id']}",
        json={"employee_count": 300, "owner_id": reps["avery"].id},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["employee_count"] == 300
    assert body["owner"] == {"id": reps["avery"].id, "name": "Avery Cole"}
    unchanged = {"name", "industry", "annual_revenue", "region", "website", "created_at"}
    assert {key: body[key] for key in unchanged} == {key: account[key] for key in unchanged}
    assert client.get(f"/api/v1/accounts/{account['id']}").json()["employee_count"] == 300


def test_patch_account_with_null_owner_clears_it(client, reps, make_account):
    account = make_account(owner_id=reps["zoe"].id)

    body = client.patch(f"/api/v1/accounts/{account['id']}", json={"owner_id": None}).json()

    assert body["owner_id"] is None
    assert body["owner"] is None


def test_patch_account_reports_its_contact_count(client, make_account, make_contact):
    account = make_account()
    make_contact(account["id"])

    body = client.patch(f"/api/v1/accounts/{account['id']}", json={"region": "East"}).json()

    assert body["region"] == "East"
    assert body["contact_count"] == 1


def test_patch_unknown_account_is_404(client):
    response = client.patch("/api/v1/accounts/7", json={"name": "Anything"})

    assert response.status_code == 404
    assert response.json() == {"detail": "Account 7 not found"}


def test_patch_account_with_unknown_owner_is_404(client, make_account):
    account = make_account()

    response = client.patch(f"/api/v1/accounts/{account['id']}", json={"owner_id": 9})

    assert response.status_code == 404
    assert response.json() == {"detail": "Rep 9 not found"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("name", ""),
        ("name", None),
        ("industry", "x" * 81),
        ("employee_count", 0),
        ("annual_revenue", "-1"),
        ("region", None),
        ("website", "x" * 201),
    ],
)
def test_patch_account_validates_like_create(client, make_account, field, value):
    account = make_account()

    response = client.patch(f"/api/v1/accounts/{account['id']}", json={field: value})

    assert response.status_code == 422
