from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import Base, get_engine
from app.main import app
from app.models import Rep


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def client(sqlite_engine):
    app.dependency_overrides[get_engine] = lambda: sqlite_engine
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


@pytest.fixture
def reps(sqlite_engine):
    """Two reps, inserted out of name order, as the seed data would create them."""
    with Session(sqlite_engine, expire_on_commit=False) as session:
        zoe = Rep(
            name="Zoe Park",
            email="zoe.park@pragmattie-sync.example",
            region="West",
            quarterly_quota=Decimal("250000.00"),
        )
        avery = Rep(
            name="Avery Cole",
            email="avery.cole@pragmattie-sync.example",
            region="East",
            quarterly_quota=Decimal("300000.00"),
        )
        session.add_all([zoe, avery])
        session.commit()
        return {"zoe": zoe, "avery": avery}


@pytest.fixture
def make_account(client):
    def make(**overrides):
        payload = {
            "name": "Northwind Traders",
            "industry": "Retail",
            "employee_count": 250,
            "annual_revenue": "12500000.00",
            "region": "West",
        }
        payload.update(overrides)
        response = client.post("/api/v1/accounts", json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    return make


@pytest.fixture
def make_contact(client):
    def make(account_id, **overrides):
        payload = {
            "account_id": account_id,
            "first_name": "Jordan",
            "last_name": "Lee",
            "email": "jordan.lee@northwind.example",
        }
        payload.update(overrides)
        response = client.post("/api/v1/contacts", json=payload)
        assert response.status_code == 201, response.text
        return response.json()

    return make
