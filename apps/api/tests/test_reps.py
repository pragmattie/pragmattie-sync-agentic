def test_list_reps_is_empty_without_seed_data(client):
    response = client.get("/api/v1/reps")

    assert response.status_code == 200
    assert response.json() == []


def test_list_reps_returns_a_plain_array_ordered_by_name(client, reps):
    response = client.get("/api/v1/reps")

    assert response.status_code == 200
    assert response.json() == [
        {
            "id": reps["avery"].id,
            "name": "Avery Cole",
            "email": "avery.cole@pragmattie-sync.example",
            "region": "East",
            "quarterly_quota": "300000.00",
        },
        {
            "id": reps["zoe"].id,
            "name": "Zoe Park",
            "email": "zoe.park@pragmattie-sync.example",
            "region": "West",
            "quarterly_quota": "250000.00",
        },
    ]
