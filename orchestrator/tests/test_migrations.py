from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from sdlc.migrations_filter import VERSION_TABLE, include_name

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


def test_history_has_a_single_initial_head():
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0001_initial"]
    assert script.get_revision("0001_initial").down_revision is None
