from sqlalchemy import create_engine

from app.db import get_engine
from app.main import app


def test_health_reports_ok_when_database_is_reachable(client):
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "crm-api", "database": "ok"}


def test_health_reports_unavailable_when_database_is_down(client):
    broken_engine = create_engine("sqlite:////nonexistent-dir/no-such-database.db")
    app.dependency_overrides[get_engine] = lambda: broken_engine

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "crm-api", "database": "unavailable"}
