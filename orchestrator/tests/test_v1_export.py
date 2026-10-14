import json
from datetime import datetime
from pathlib import Path

import pytest

from sdlc.v1_export import ExportError, load_export

FIXTURE = Path(__file__).parent / "fixtures" / "v1_export_pass.json"
REPO = "pragmattie/pragmattie-sync-agentic"


@pytest.fixture(autouse=True)
def repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", REPO)


def _write(tmp_path, data):
    path = tmp_path / "export.json"
    path.write_text(json.dumps(data))
    return path


def test_a_valid_export_loads_with_naive_utc_times():
    export = load_export(FIXTURE)

    assert export.repository == REPO
    assert export.exported_at == datetime(2026, 10, 14, 9, 0)
    assert export.exported_at.tzinfo is None
    first = export.decisions[0]
    assert first.agent == "pr_risk"
    assert first.created_at == datetime(2026, 10, 5, 9, 30)
    assert first.output["floors"] == []
    assert export.approvals[0].pr == 8
    assert export.approvals[0].decided_at == datetime(2026, 10, 6, 13, 0)


def test_an_offset_time_is_converted_to_naive_utc(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["decisions"][0]["created_at"] = "2026-10-05T11:30:00+02:00"

    export = load_export(_write(tmp_path, data))

    assert export.decisions[0].created_at == datetime(2026, 10, 5, 9, 30)


def test_an_export_for_another_repository_is_refused(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["repository"] = "pragmattie/elsewhere"

    with pytest.raises(ExportError) as error:
        load_export(_write(tmp_path, data))

    assert str(error.value) == f"This export is for pragmattie/elsewhere, not {REPO}."


def test_a_decision_missing_a_field_is_refused_by_field_and_id(tmp_path):
    data = json.loads(FIXTURE.read_text())
    del data["decisions"][1]["inputs_digest"]

    with pytest.raises(ExportError) as error:
        load_export(_write(tmp_path, data))

    assert str(error.value) == "Decision 5002 is missing the field 'inputs_digest'."
