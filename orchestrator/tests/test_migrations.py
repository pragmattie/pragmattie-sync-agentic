import os
from datetime import date, datetime
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, delete, inspect, select
from sqlalchemy.orm import Session

from sdlc.config import get_settings
from sdlc.migrations_filter import VERSION_TABLE, include_name
from sdlc.tables import Forecast

SDLC_TABLES = (
    "sdlc_engineers",
    "sdlc_sprints",
    "sdlc_issues",
    "sdlc_pull_requests",
    "sdlc_ci_runs",
    "sdlc_deployments",
    "sdlc_incidents",
    "sdlc_agent_decisions",
    "sdlc_gate_status",
    "sdlc_approvals",
    "sdlc_epics",
    "sdlc_forecasts",
)
BIG_SEED = 2**32 - 1  # the largest crc32, above a signed INT's 2**31 - 1

SERVICE_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("table", ["sdlc_runs", "sdlc_alembic_version"])
def test_filter_keeps_sdlc_tables(table):
    assert include_name(table, "table", {"schema_name": None}) is True


@pytest.mark.parametrize("table", ["accounts", "leads", "alembic_version", "sdlcruns"])
def test_filter_ignores_crm_tables(table):
    assert include_name(table, "table", {"schema_name": None}) is False


@pytest.mark.parametrize(
    ("name", "type_"),
    [("accounts_id_idx", "index"), ("status", "column"), (None, "schema")],
)
def test_filter_passes_non_table_names_through(name, type_):
    assert include_name(name, type_, {"table_name": "sdlc_runs"}) is True


def test_version_table_is_prefixed():
    assert VERSION_TABLE == "sdlc_alembic_version"
    assert include_name(VERSION_TABLE, "table", {"schema_name": None}) is True


def _alembic_config() -> Config:
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    return config


def test_history_has_a_single_head():
    script = ScriptDirectory.from_config(_alembic_config())

    assert script.get_heads() == ["0007_forecasts"]
    assert script.get_revision("0007_forecasts").down_revision == "0006_epics"
    assert script.get_revision("0006_epics").down_revision == "0005_approvals"
    assert script.get_revision("0005_approvals").down_revision == "0004_gate_status"
    assert script.get_revision("0004_gate_status").down_revision == "0003_agent_decisions"
    assert script.get_revision("0003_agent_decisions").down_revision == "0002_engineering_tables"
    assert script.get_revision("0002_engineering_tables").down_revision == "0001_initial"
    assert script.get_revision("0001_initial").down_revision is None


def test_upgrade_check_and_downgrade_round_trip(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'migrations.db'}")
    get_settings.cache_clear()
    config = _alembic_config()
    try:
        command.upgrade(config, "head")
        engine = create_engine(get_settings().database_url)
        try:
            assert set(SDLC_TABLES) <= set(inspect(engine).get_table_names())
            _assert_seed_round_trips(engine)
        finally:
            engine.dispose()
        command.check(config)
        command.downgrade(config, "base")
        engine = create_engine(get_settings().database_url)
        try:
            remaining = set(inspect(engine).get_table_names())
        finally:
            engine.dispose()
        assert remaining.isdisjoint(SDLC_TABLES)
    finally:
        get_settings.cache_clear()


def _assert_seed_round_trips(engine) -> None:
    """A seed above 2**31 is stored and read back unchanged; the row is removed afterwards."""
    with Session(engine) as db:
        row = Forecast(
            created_at=datetime(2026, 10, 10, 9),
            as_of=date(2026, 10, 10),
            kind="sprint",
            subject="Seed check",
            source="synthetic",
            trigger="manual",
            inputs_hash="0" * 64,
            remaining_items=0,
            remaining_points=0,
            throughput_mean=0.0,
            history_days=84,
            runs=1,
            seed=BIG_SEED,
            at_risk=[],
        )
        db.add(row)
        db.commit()
        try:
            db.expire_all()
            assert db.scalar(select(Forecast.seed).where(Forecast.id == row.id)) == BIG_SEED
        finally:
            db.execute(delete(Forecast).where(Forecast.id == row.id))
            db.commit()


def test_a_seed_above_2_31_is_stored_on_mysql():
    """Runs only when ``DATABASE_URL`` is set to MySQL, as in CI's MySQL job."""
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("mysql"):
        pytest.skip("DATABASE_URL is not MySQL")
    get_settings.cache_clear()
    try:
        command.upgrade(_alembic_config(), "head")
        engine = create_engine(url)
        try:
            _assert_seed_round_trips(engine)
        finally:
            engine.dispose()
    finally:
        get_settings.cache_clear()
