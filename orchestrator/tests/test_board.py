import re
from pathlib import Path

import pytest

from sdlc import board
from sdlc.board import FIELDS, STATUSES, check, load_board, main, setup
from sdlc.github_client import GitHubError
from sdlc.tables import MODULES

FORBIDDEN = (
    "deleteProjectV2Field",
    "deleteProjectV2Item",
    "deleteProjectV2",
    "updateProjectV2Field",
)


def _options(prefix, names):
    return [{"id": f"{prefix}-{index}", "name": name} for index, name in enumerate(names)]


def _existing_fields():
    """The board as created on 2026-10-04: Status, Points, Module and Forecast tier."""
    return [
        {"id": "F-title", "name": "Title", "dataType": "TITLE"},
        {
            "id": "F-status",
            "name": "Status",
            "dataType": "SINGLE_SELECT",
            "options": _options("S", STATUSES),
        },
        {"id": "F-points", "name": "Points", "dataType": "NUMBER"},
        {
            "id": "F-module",
            "name": "Module",
            "dataType": "SINGLE_SELECT",
            "options": _options("M", MODULES),
        },
        {
            "id": "F-forecast",
            "name": "Forecast tier",
            "dataType": "SINGLE_SELECT",
            "options": _options("FT", ("T0", "T1", "T2", "T3")),
        },
        {"id": "F-milestone", "name": "Milestone", "dataType": "MILESTONE"},
    ]


class FakeGraphQL:
    """Answers the board query from in-memory fields and logs every query and mutation it gets."""

    def __init__(self, fields=None):
        self.fields = _existing_fields() if fields is None else fields
        self.calls: list[tuple[str, dict]] = []

    @property
    def mutations(self):
        return [(query, variables) for query, variables in self.calls if "mutation" in query]

    def field(self, name):
        return next(node for node in self.fields if node["name"] == name)

    def graphql(self, query, **variables):
        self.calls.append((query, variables))
        if "createProjectV2Field" in query:
            spec = variables["input"]
            node = {
                "id": f"F-new-{spec['name']}",
                "name": spec["name"],
                "dataType": spec["dataType"],
            }
            if "singleSelectOptions" in spec:
                node["options"] = _options(
                    spec["name"], [o["name"] for o in spec["singleSelectOptions"]]
                )
            self.fields.append(node)
            return {"createProjectV2Field": {"projectV2Field": {"id": node["id"]}}}
        if "mutation" in query:
            raise AssertionError(f"unexpected mutation: {query}")
        assert variables == {"owner": "pragmattie", "number": 1}
        return {
            "organization": {
                "projectV2": {
                    "id": "PVT_1",
                    "title": "PragMattie Sync delivery",
                    "fields": {"nodes": [dict(node) for node in self.fields] + [{}]},
                }
            }
        }


def _ready_fields():
    fields = _existing_fields()
    fields.append(
        {
            "id": "F-tier",
            "name": "Tier",
            "dataType": "SINGLE_SELECT",
            "options": _options("T", ("T0", "T1", "T2", "T3")),
        }
    )
    fields.append({"id": "F-risk", "name": "Risk", "dataType": "NUMBER"})
    return fields


def _kinds(problems):
    return [problem.kind for problem in problems]


def test_load_board_parses_fields_options_and_ids():
    loaded = load_board(FakeGraphQL())
    assert loaded.project_id == "PVT_1"
    assert loaded.title == "PragMattie Sync delivery"
    assert loaded.status_field_id == "F-status"
    assert list(loaded.statuses) == list(STATUSES)
    assert loaded.statuses["Gated"] == "S-4"
    assert loaded.fields["Points"].id == "F-points"
    assert loaded.fields["Points"].data_type == "NUMBER"
    assert loaded.fields["Points"].options == {}
    assert list(loaded.fields["Module"].options) == list(MODULES)
    assert loaded.fields["Module"].options["pipeline"] == "M-2"
    assert loaded.fields["Forecast tier"].options == {
        "T0": "FT-0",
        "T1": "FT-1",
        "T2": "FT-2",
        "T3": "FT-3",
    }
    assert "Tier" not in loaded.fields
    assert loaded.settings_url == "https://github.com/orgs/pragmattie/projects/1/settings"


def test_load_board_without_the_project_raises():
    fake = FakeGraphQL()
    fake.graphql = lambda query, **variables: {"organization": {"projectV2": None}}
    with pytest.raises(GitHubError, match="No project 1"):
        load_board(fake)


def test_a_ready_board_has_no_problems():
    assert check(load_board(FakeGraphQL(_ready_fields()))) == []


def test_check_reports_the_missing_fields():
    problems = check(load_board(FakeGraphQL()))
    assert [(p.kind, p.field_name) for p in problems] == [
        ("missing_field", "Tier"),
        ("missing_field", "Risk"),
    ]
    assert all(p.creatable for p in problems)


def test_check_reports_a_missing_status_option():
    fake = FakeGraphQL(_ready_fields())
    fake.field("Status")["options"] = _options("S", [s for s in STATUSES if s != "Gated"])
    problems = check(load_board(fake))
    assert _kinds(problems) == ["missing_status"]
    assert '"Gated"' in problems[0].message
    assert "projects/1/settings → Fields → Status → Add option" in problems[0].fix
    assert not problems[0].creatable


def test_check_reports_status_options_out_of_order():
    fake = FakeGraphQL(_ready_fields())
    order = ["Backlog", "In progress", "Triaged", "In review", "Gated", "Merged", "Production"]
    fake.field("Status")["options"] = _options("S", order)
    problems = check(load_board(fake))
    assert _kinds(problems) == ["status_order"]
    assert ", ".join(STATUSES) in problems[0].fix


def test_extra_status_options_are_left_alone():
    fake = FakeGraphQL(_ready_fields())
    fake.field("Status")["options"] = _options("S", ["Backlog", "Icebox", *STATUSES[1:]])
    assert check(load_board(fake)) == []


def test_check_reports_a_field_of_the_wrong_type():
    fake = FakeGraphQL(_ready_fields())
    fake.field("Risk")["dataType"] = "TEXT"
    problems = check(load_board(fake))
    assert _kinds(problems) == ["wrong_type"]
    assert "Risk field is text, not number" in problems[0].message
    assert not problems[0].creatable


def test_check_reports_a_missing_single_select_option():
    fake = FakeGraphQL(_ready_fields())
    fake.field("Module")["options"] = _options("M", [m for m in MODULES if m != "forecasting"])
    problems = check(load_board(fake))
    assert _kinds(problems) == ["missing_option"]
    assert 'Module → Add option "forecasting"' in problems[0].fix


def test_setup_apply_creates_only_the_missing_tier_and_risk_fields(capsys):
    fake = FakeGraphQL()
    assert setup(fake, apply=True) == []
    created = [variables["input"] for _, variables in fake.mutations]
    assert created == [
        {
            "projectId": "PVT_1",
            "dataType": "SINGLE_SELECT",
            "name": "Tier",
            "singleSelectOptions": [
                {"name": tier, "color": "GRAY", "description": ""}
                for tier in ("T0", "T1", "T2", "T3")
            ],
        },
        {"projectId": "PVT_1", "dataType": "NUMBER", "name": "Risk"},
    ]
    assert all("createProjectV2Field" in query for query, _ in fake.mutations)
    assert check(load_board(fake)) == []
    assert setup(fake, apply=True) == []  # a second run has nothing to do
    assert len(fake.mutations) == 2
    out = capsys.readouterr().out
    assert "Created the Tier field (single select: T0, T1, T2, T3)." in out


def test_setup_without_apply_sends_no_mutation(capsys):
    fake = FakeGraphQL()
    assert setup(fake) == []
    assert fake.mutations == []
    out = capsys.readouterr().out
    assert "The Tier field is missing." in out
    assert "Dry run only" in out


@pytest.mark.parametrize("broken", ["status_order", "missing_option"])
def test_a_problem_with_an_existing_field_exits_1_with_no_mutation(broken, capsys):
    fake = FakeGraphQL()  # Tier and Risk are missing too
    if broken == "status_order":
        fake.field("Status")["options"] = _options("S", reversed(STATUSES))
    else:
        fake.field("Module")["options"] = _options("M", MODULES[:-1])
    assert main(["setup", "--apply"], client=fake) == 1
    assert fake.mutations == []
    out = capsys.readouterr().out
    assert "Fix: https://github.com/orgs/pragmattie/projects/1/settings → Fields" in out
    assert "Nothing created" in out


def test_setup_prints_the_view_layout_once(capsys):
    assert main(["setup"], client=FakeGraphQL(_ready_fields())) == 0
    out = capsys.readouterr().out
    assert out.count('View "Delivery"') == 1
    assert "Board layout, columns by Status, swimlanes by Milestone" in out
    assert "fields Points, Module, Forecast tier, Tier and Risk shown" in out


def test_a_github_error_exits_with_its_message():
    fake = FakeGraphQL()

    def fail(query, **variables):
        raise GitHubError("Resource not accessible by integration")

    fake.graphql = fail
    with pytest.raises(SystemExit, match="Resource not accessible"):
        main(["setup"], client=fake)


def test_no_code_path_deletes_renames_or_replaces_options():
    scenarios = [FakeGraphQL(), FakeGraphQL(_ready_fields())]
    for fields in (_ready_fields(), _existing_fields()):
        broken = FakeGraphQL(fields)
        broken.field("Status")["options"] = _options("S", reversed(STATUSES))
        scenarios.append(broken)
    wrong = FakeGraphQL()
    wrong.field("Points")["dataType"] = "TEXT"
    scenarios.append(wrong)
    for fake in scenarios:
        for apply in (False, True):
            main(["setup", "--apply"] if apply else ["setup"], client=fake)
    mutations = [query for fake in scenarios for query, _ in fake.mutations]
    assert mutations  # the missing fields were created somewhere
    for query in mutations:
        names = set(re.findall(r"\b(\w+ProjectV2\w*)\s*\(", query))
        assert names == {"createProjectV2Field"}
        for forbidden in FORBIDDEN:
            assert forbidden not in query
    source = Path(board.__file__).read_text()
    for forbidden in FORBIDDEN:
        assert forbidden not in source


def test_fields_match_the_spec():
    assert STATUSES == (
        "Backlog",
        "Triaged",
        "In progress",
        "In review",
        "Gated",
        "Merged",
        "Production",
    )
    assert list(FIELDS) == ["Points", "Module", "Forecast tier", "Tier", "Risk"]
    assert FIELDS["Module"] == ("SINGLE_SELECT", MODULES)
    assert FIELDS["Risk"] == ("NUMBER", ())
