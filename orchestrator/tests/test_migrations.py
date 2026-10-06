from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from sdlc.config import get_settings
from sdlc.migrations_filter import VERSION_TABLE, include_name

SDLC_TABLES = (
    "sdlc_engineers",
    "sdlc_sprints",
    "sdlc_issues",
    "sdlc_pull_requests",
    "sdlc_ci_runs",
    "sdlc_deployments",
    "sdlc_incidents",
    "sdlc_agent_decisions",
)

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

    assert script.get_heads() == ["0003_agent_decisions"]
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
