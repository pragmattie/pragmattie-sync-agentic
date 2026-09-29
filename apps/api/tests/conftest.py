import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from app.db import get_engine
from app.main import app


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    yield engine
    engine.dispose()


@pytest.fixture
def client(sqlite_engine):
    app.dependency_overrides[get_engine] = lambda: sqlite_engine
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
