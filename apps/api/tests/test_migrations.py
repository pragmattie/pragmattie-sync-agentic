from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.migrations_filter import include_name

SERVICE_ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("table", ["accounts", "leads", "alembic_version"])
def test_filter_keeps_crm_tables(table):
    assert include_name(table, "table", {"schema_name": None}) is True


@pytest.mark.parametrize("table", ["sdlc_runs", "sdlc_alembic_version"])
def test_filter_ignores_orchestrator_tables(table):
    assert include_name(table, "table", {"schema_name": None}) is False


@pytest.mark.parametrize(
    ("name", "type_"),
    [("sdlc_runs_id_idx", "index"), ("sdlc_status", "column"), (None, "schema")],
)
def test_filter_passes_non_table_names_through(name, type_):
    assert include_name(name, type_, {"table_name": "accounts"}) is True


def test_history_is_a_single_line_from_initial():
    config = Config(str(SERVICE_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(SERVICE_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)

    assert script.get_heads() == ["0002_reps_accounts_contacts"]
    assert script.get_revision("0001_initial").down_revision is None
    assert script.get_revision("0002_reps_accounts_contacts").down_revision == "0001_initial"
