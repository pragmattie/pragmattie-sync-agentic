from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app


def test_health_reports_ok_database(tmp_path: Path) -> None:
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'health.db'}")
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "crm-api", "database": "ok"}


def test_health_reports_unavailable_database() -> None:
    settings = Settings(database_url="sqlite:////no/such/directory/health.db")
    client = TestClient(create_app(settings))

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "crm-api", "database": "unavailable"}


def test_cors_origins_default_to_the_documented_hosts() -> None:
    settings = Settings()

    assert settings.cors_origins == [
        "http://localhost:5173",
        "http://pragmattie-sync.localhost",
    ]
