import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event

from sdlc.db import get_engine
from sdlc.main import app


def _no_pysqlite_transactions(connection, record):
    connection.isolation_level = None


def _begin(connection):
    connection.exec_driver_sql("BEGIN")


@pytest.fixture
def sqlite_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    # pysqlite emits no BEGIN, so a SAVEPOINT's release would commit; SQLAlchemy's recipe makes
    # savepoints behave as on MySQL (the collector inserts new rows in one).
    event.listen(engine, "connect", _no_pysqlite_transactions)
    event.listen(engine, "begin", _begin)
    yield engine
    engine.dispose()


@pytest.fixture
def client(sqlite_engine):
    app.dependency_overrides[get_engine] = lambda: sqlite_engine
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
