from datetime import datetime

import pytest


@pytest.fixture
def account(make_account):
    return make_account()


def test_create_contact_returns_it(client, account):
    response = client.post(
        "/api/v1/contacts",
        json={
            "account_id": account["id"],
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@northwind.example",
            "title": "VP Sales",
            "phone": "+1 555 0100",
        },
    )

    assert response.status_code == 201
    body = response.json()
    datetime.fromisoformat(body.pop("created_at"))
    assert isinstance(body.pop("id"), int)
    assert body == {
        "account_id": account["id"],
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@northwind.example",
        "title": "VP Sales",
        "phone": "+1 555 0100",
    }


def test_create_contact_without_optional_fields(make_contact, account):
    contact = make_contact(account["id"])

    assert contact["title"] is None
    assert contact["phone"] is None


def test_create_contact_for_unknown_account_is_404(client):
    response = client.post(
        "/api/v1/contacts",
        json={
            "account_id": 7,
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@northwind.example",
        },
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "Account 7 not found"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("first_name", ""),
        ("first_name", "x" * 81),
        ("last_name", ""),
        ("last_name", "x" * 81),
        ("email", "not-an-email"),
        ("email", "jordan@"),
        ("email", "x" * 190 + "@northwind.example"),
        ("title", "x" * 121),
        ("phone", "x" * 41),
    ],
)
def test_create_contact_rejects_values_outside_the_limits(client, account, field, value):
    payload = {
        "account_id": account["id"],
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@northwind.example",
        field: value,
    }

    assert client.post("/api/v1/contacts", json=payload).status_code == 422


@pytest.mark.parametrize(
    ("field", "value"),
    [("first_name", "x" * 80), ("last_name", "x" * 80), ("title", "x" * 120), ("phone", "x" * 40)],
)
def test_create_contact_accepts_values_at_the_limits(make_contact, account, field, value):
    assert make_contact(account["id"], **{field: value})[field] == value


@pytest.mark.parametrize("field", ["account_id", "first_name", "last_name", "email"])
def test_create_contact_requires_fields(client, account, field):
    payload = {
        "account_id": account["id"],
        "first_name": "Jordan",
        "last_name": "Lee",
        "email": "jordan.lee@northwind.example",
    }
    del payload[field]

    assert client.post("/api/v1/contacts", json=payload).status_code == 422


def test_list_contacts_orders_by_last_name(client, account, make_contact):
    make_contact(account["id"], last_name="Young", email="y@northwind.example")
    make_contact(account["id"], last_name="Adams", email="a@northwind.example")
    make_contact(account["id"], last_name="Lee", email="l@northwind.example")

    body = client.get("/api/v1/contacts").json()

    assert body["total"] == 3
    assert [item["last_name"] for item in body["items"]] == ["Adams", "Lee", "Young"]


def test_list_contacts_filters_by_account(client, make_account, make_contact):
    northwind = make_account(name="Northwind Traders")
    acme = make_account(name="Acme Logistics")
    make_contact(northwind["id"], last_name="North")
    make_contact(acme["id"], last_name="Acme")

    body = client.get("/api/v1/contacts", params={"account_id": acme["id"]}).json()

    assert body["total"] == 1
    assert [item["last_name"] for item in body["items"]] == ["Acme"]


@pytest.mark.parametrize(
    ("q", "expected"),
    [("RIV", ["Rivera"]), ("mOrGaN", ["Chen"]), ("@GLOBEX", ["Singh"]), ("zzz", [])],
    ids=["last-name", "first-name", "email", "no-match"],
)
def test_list_contacts_filters_by_name_or_email_case_insensitively(
    client, account, make_contact, q, expected
):
    make_contact(account["id"], first_name="Ana", last_name="Rivera", email="ana@acme.example")
    make_contact(account["id"], first_name="Morgan", last_name="Chen", email="mc@acme.example")
    make_contact(account["id"], first_name="Priya", last_name="Singh", email="p@globex.example")

    body = client.get("/api/v1/contacts", params={"q": q}).json()

    assert body["total"] == len(expected)
    assert [item["last_name"] for item in body["items"]] == expected


def test_list_contacts_pages_after_counting_every_match(client, account, make_contact):
    for last_name in ["Evans", "Brown", "Davis", "Adams", "Clark"]:
        make_contact(account["id"], last_name=last_name)

    body = client.get("/api/v1/contacts", params={"limit": 2, "offset": 2}).json()

    assert body["total"] == 5
    assert [item["last_name"] for item in body["items"]] == ["Clark", "Davis"]


@pytest.mark.parametrize(
    "params", [{"limit": 0}, {"limit": 201}, {"offset": -1}], ids=["limit0", "limit201", "neg"]
)
def test_list_contacts_rejects_paging_outside_the_bounds(client, params):
    assert client.get("/api/v1/contacts", params=params).status_code == 422


def test_list_contacts_offset_past_the_end_returns_no_items_but_the_full_total(
    client, account, make_contact
):
    for last_name in ["Evans", "Brown", "Davis"]:
        make_contact(account["id"], last_name=last_name)

    body = client.get("/api/v1/contacts", params={"offset": 10}).json()

    assert body == {"items": [], "total": 3}
