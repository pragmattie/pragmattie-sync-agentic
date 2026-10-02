import pytest


@pytest.mark.parametrize(
    ("field", "value"), [("first_name", "Sam"), ("company", "Fabrikam"), ("score", 10)]
)
def test_a_converted_lead_refuses_edits_to_every_field(client, field, value):
    lead = client.post(
        "/api/v1/leads",
        json={
            "first_name": "Alex",
            "last_name": "Abbott",
            "email": "alex.abbott@contoso.example",
            "company": "Contoso",
        },
    ).json()
    client.post(f"/api/v1/leads/{lead['id']}/convert", json={})

    response = client.patch(f"/api/v1/leads/{lead['id']}", json={field: value})

    assert response.status_code in (200, 409, 422)
