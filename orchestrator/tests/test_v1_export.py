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


@pytest.mark.parametrize(
    "text, message",
    [
        ("{not json", "isn't valid JSON: "),
        ("[]", "The export isn't a JSON object."),
    ],
)
def test_a_file_that_is_not_a_json_object_is_refused(tmp_path, text, message):
    path = tmp_path / "export.json"
    path.write_text(text)

    with pytest.raises(ExportError) as error:
        load_export(path)

    assert message in str(error.value)


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ExportError) as error:
        load_export(tmp_path / "nowhere.json")

    assert str(error.value).startswith("Can't read the export ")


def test_a_decision_that_is_not_an_object_is_refused(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["decisions"][2] = "pr_risk"

    with pytest.raises(ExportError) as error:
        load_export(_write(tmp_path, data))

    assert str(error.value) == "Decision 3 in the file isn't a JSON object."


def test_a_bad_time_is_refused_by_field_and_id(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["decisions"][0]["created_at"] = "yesterday"

    with pytest.raises(ExportError) as error:
        load_export(_write(tmp_path, data))

    assert str(error.value) == "Decision 5001 has a bad time in 'created_at': 'yesterday'."


def test_an_approval_with_a_bad_time_is_refused(tmp_path):
    data = json.loads(FIXTURE.read_text())
    data["approvals"][0]["decided_at"] = "soon"

    with pytest.raises(ExportError) as error:
        load_export(_write(tmp_path, data))

    assert str(error.value) == "The approval for PR 8 has a bad time in 'decided_at': 'soon'."
