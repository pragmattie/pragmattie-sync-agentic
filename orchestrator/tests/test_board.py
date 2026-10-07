import re
from pathlib import Path

import pytest
import yaml

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


# Keeping the board current (sync)

RISK_COMMENT = (
    "<!-- pragmattie-risk-gate -->\n## Risk gate: T1\n\n"
    "**Score 37/100**: rubric 37, model adjustment +0.\n\n**Why**\n1. Touches the pipeline."
)
READY = ["module:pipeline", "type:feature", "priority:p2", "points:3", "forecast:T2"]
PLAN = "## Plan\n\n**Files I'll change or create**\n- `apps/api/app/leads.py`"
SYNC_MUTATIONS = {"addProjectV2ItemById", "updateProjectV2ItemFieldValue"}
SYNC_MUTATIONS |= {"clearProjectV2ItemFieldValue"}
NEVER = (
    "deleteProjectV2Item",
    "archiveProjectV2Item",
    "unarchiveProjectV2Item",
    "updateIssue",
    "closeIssue",
    "addProjectV2DraftIssue",
    "convertProjectV2DraftIssueItemToIssue",
    "updateProjectV2ItemPosition",
)


def _issue(labels=(), state="OPEN", reason=None, comments=()):
    return {
        "id": "I_1",
        "number": 1,
        "state": state,
        "state_reason": reason,
        "labels": list(labels),
        "comments": [{"body": body, "bot": bot} for body, bot in comments],
    }


def _pr(state="OPEN", draft=False, merged=False, gate=None, labels=(), comments=()):
    return {
        "number": 9,
        "state": "MERGED" if merged else state,
        "draft": draft,
        "merged": merged,
        "gate": gate,
        "labels": list(labels),
        "comments": list(comments),
    }


COLUMN_CASES = [
    ("closed not planned", _issue(READY, "CLOSED", "NOT_PLANNED"), [], None),
    (
        "closed not planned, merged PR",
        _issue(state="CLOSED", reason="NOT_PLANNED"),
        [_pr(merged=True)],
        None,
    ),
    ("closed completed", _issue(READY, "CLOSED", "COMPLETED"), [], "Merged"),
    ("merged PR on an open issue", _issue(READY), [_pr(merged=True)], "Merged"),
    (
        "merged PR beats an open one",
        _issue(READY),
        [_pr(gate="pending"), _pr(merged=True)],
        "Merged",
    ),
    ("open PR, gate pending", _issue(READY), [_pr(gate="pending")], "Gated"),
    ("open PR, gate failing", _issue(READY), [_pr(gate="failure")], "Gated"),
    ("open PR, gate success", _issue(READY), [_pr(gate="success")], "In review"),
    ("gated beats in review", _issue(READY), [_pr(gate="success"), _pr(gate="pending")], "Gated"),
    ("open PR not scored yet", _issue(READY), [_pr()], "In progress"),
    ("draft PR", _issue(READY), [_pr(draft=True, gate="pending")], "In progress"),
    ("closed unmerged PR is ignored", _issue(READY), [_pr(state="CLOSED")], "Triaged"),
    ("agent-ready, no PR", _issue([*READY, "agent-ready"]), [], "In progress"),
    ("spec-draft + needs-info", _issue(["spec-draft", "needs-info", *READY]), [], "Backlog"),
    ("needs-info only", _issue([*READY, "needs-info"]), [], "Backlog"),
    ("no module label", _issue(["type:feature", "points:3"]), [], "Backlog"),
    ("no points label", _issue(["type:feature", "module:pipeline"]), [], "Backlog"),
    ("no labels", _issue(), [], "Backlog"),
    ("module, type, priority, points", _issue(READY), [], "Triaged"),
    (
        "plan waiting for the person",
        _issue([*READY, "agent-ready", "plan-proposed"], comments=[(PLAN, True)]),
        [],
        "Triaged",
    ),
    (
        "plan approved by a person",
        _issue(
            [*READY, "agent-ready", "plan-proposed"],
            comments=[(PLAN, True), ("/approve-plan\n\n- Settings: yes.", False)],
        ),
        [],
        "In progress",
    ),
    (
        "approval by a bot doesn't count",
        _issue(
            [*READY, "agent-ready", "plan-proposed"],
            comments=[(PLAN, True), ("/approve-plan", True)],
        ),
        [],
        "Triaged",
    ),
    (
        "approval before a newer plan doesn't count",
        _issue(
            [*READY, "agent-ready", "plan-proposed"],
            comments=[(PLAN, True), ("/approve-plan", False), (PLAN, True)],
        ),
        [],
        "Triaged",
    ),
    (
        "a waiting plan with a PR open follows the PR",
        _issue([*READY, "agent-ready", "plan-proposed"], comments=[(PLAN, True)]),
        [_pr(draft=True)],
        "In progress",
    ),
]


@pytest.mark.parametrize(
    "issue, prs, expected", [case[1:] for case in COLUMN_CASES], ids=[c[0] for c in COLUMN_CASES]
)
def test_column_for(issue, prs, expected):
    assert board.column_for(issue, prs) == expected


def test_fields_for_reads_labels_tier_and_the_risk_score():
    prs = [_pr(gate="success", labels=["tier:T1"], comments=["Thanks!", RISK_COMMENT])]
    assert board.fields_for(_issue(READY), prs) == {
        "Points": 3.0,
        "Module": "pipeline",
        "Forecast tier": "T2",
        "Tier": "T1",
        "Risk": 37,
    }


def test_fields_for_uses_the_deciding_pr():
    prs = [
        _pr(state="CLOSED", labels=["tier:T3"], comments=[RISK_COMMENT.replace("37", "80")]),
        _pr(merged=True, labels=["tier:T1"], comments=[RISK_COMMENT]),
        _pr(gate="pending", labels=["tier:T2"], comments=[RISK_COMMENT.replace("37", "55")]),
    ]
    fields = board.fields_for(_issue(READY), prs)
    assert (fields["Tier"], fields["Risk"]) == ("T1", 37)


def test_fields_for_leaves_risk_empty_without_a_score():
    no_score = "<!-- pragmattie-risk-gate -->\nThe risk gate is waiting for CI."
    for comments in ([], ["**Score 50/100** without the marker"], [no_score]):
        prs = [_pr(gate="pending", labels=["tier:T2"], comments=comments)]
        assert board.fields_for(_issue(READY), prs)["Risk"] is None
    assert board.fields_for(_issue(["spec-draft"]), []) == {
        "Points": None,
        "Module": None,
        "Forecast tier": None,
        "Tier": None,
        "Risk": None,
    }


def _issue_node(number, labels=(), state="OPEN", reason=None, comments=(), prs=()):
    return {
        "id": f"I_{number}",
        "number": number,
        "state": state,
        "stateReason": reason,
        "labels": {"nodes": [{"name": label} for label in labels]},
        "comments": {
            "nodes": [
                {"body": body, "author": {"login": "x", "__typename": "Bot" if bot else "User"}}
                for body, bot in comments
            ]
        },
        "closedByPullRequestsReferences": {"nodes": list(prs)},
    }


def _pr_node(gate_state=None, draft=False, merged=False, labels=(), comments=(), check_run=False):
    contexts = [{"__typename": "StatusContext", "context": "CI", "state": "SUCCESS"}]
    if gate_state and check_run:
        contexts.append(
            {
                "__typename": "CheckRun",
                "name": "risk-gate",
                "status": "COMPLETED",
                "conclusion": gate_state,
            }
        )
    elif gate_state:
        contexts.append(
            {"__typename": "StatusContext", "context": "risk-gate", "state": gate_state}
        )
    return {
        "number": 90,
        "state": "MERGED" if merged else "OPEN",
        "isDraft": draft,
        "merged": merged,
        "labels": {"nodes": [{"name": label} for label in labels]},
        "comments": {"nodes": [{"body": body} for body in comments]},
        "commits": {
            "nodes": [{"commit": {"statusCheckRollup": {"contexts": {"nodes": contexts}}}}]
        },
    }


def _value(name, value):
    key = "number" if isinstance(value, int | float) else "name"
    return {key: value, "field": {"name": name}}


def _item(item_id, issue_number, **values):
    return {
        "id": item_id,
        "content": {"__typename": "Issue", "id": f"I_{issue_number}", "number": issue_number},
        "fieldValues": {
            "nodes": [{}]
            + [_value(name.replace("_", " ").capitalize(), v) for name, v in values.items()]
        },
    }


class FakeBoard(FakeGraphQL):
    """The ready board with items, plus open and recently closed issues, served in pages of one."""

    repo = "pragmattie/pragmattie-sync-agentic"

    def __init__(self, items, open_issues, closed_issues=()):
        super().__init__(_ready_fields())
        self.items = list(items)
        self.issues = {"is:open": list(open_issues), "is:closed": list(closed_issues)}

    @staticmethod
    def _page(nodes, after):
        index = int(after or 0)
        more = index + 1 < len(nodes)
        return {
            "pageInfo": {"hasNextPage": more, "endCursor": str(index + 1) if more else None},
            "nodes": nodes[index : index + 1],
        }

    def graphql(self, query, **variables):
        if "addProjectV2ItemById" in query:
            self.calls.append((query, variables))
            return {"addProjectV2ItemById": {"item": {"id": f"PVTI_new_{variables['content']}"}}}
        if "ProjectV2ItemFieldValue" in query:
            self.calls.append((query, variables))
            return {}
        if "search(" in query:
            self.calls.append((query, variables))
            assert variables["q"].startswith(f"repo:{self.repo} is:issue ")
            kind = "is:open" if "is:open" in variables["q"] else "is:closed"
            return {"search": self._page(self.issues[kind], variables["after"])}
        if "items(" in query:
            self.calls.append((query, variables))
            page = self._page(self.items, variables["after"])
            return {"organization": {"projectV2": {"items": page}}}
        return super().graphql(query, **variables)


def _current_board():
    """Four issue cards, all current, plus a pull-request card and a draft card."""
    items = [
        _item("PVTI_1", 1, status="Triaged", points=3, module="pipeline", forecast_tier="T2"),
        _item("PVTI_2", 2, status="Gated", points=5, module="leads", tier="T2", risk=41),
        _item("PVTI_3", 3, status="Merged", points=2, module="leads", tier="T1", risk=12),
        _item("PVTI_4", 4, status="Backlog"),
        {"id": "PVTI_pr", "content": {"__typename": "PullRequest"}, "fieldValues": {"nodes": []}},
        {"id": "PVTI_draft", "content": {"__typename": "DraftIssue"}, "fieldValues": {}},
    ]
    risk = RISK_COMMENT.replace("37", "41")
    open_issues = [
        _issue_node(1, READY),
        _issue_node(
            2,
            ["module:leads", "points:5"],
            prs=[_pr_node("PENDING", labels=["tier:T2"], comments=[risk])],
        ),
        _issue_node(4, ["spec-draft", "needs-info"]),
    ]
    closed = [
        _issue_node(
            3,
            ["module:leads", "points:2"],
            state="CLOSED",
            reason="COMPLETED",
            prs=[
                _pr_node(
                    "SUCCESS",
                    merged=True,
                    labels=["tier:T1"],
                    comments=[RISK_COMMENT.replace("37", "12")],
                )
            ],
        ),
        _issue_node(5, READY, state="CLOSED", reason="NOT_PLANNED"),
    ]
    return FakeBoard(items, open_issues, closed)


def _mutation_names(fake):
    return [re.findall(r"\b(\w+ProjectV2\w*)\s*\(", query)[0] for query, _ in fake.mutations]


def test_an_unchanged_board_sends_no_mutation(capsys):
    fake = _current_board()
    report = board.sync(fake)
    assert fake.mutations == []
    assert (report.seen, report.added, dict(report.changed)) == (4, 0, {})
    assert "4 items seen, 0 added; changed: none." in capsys.readouterr().out


def test_a_changed_status_and_a_new_issue_send_one_update_and_one_add():
    fake = _current_board()
    fake.issues["is:open"][1]["closedByPullRequestsReferences"]["nodes"][0] = _pr_node(
        "SUCCESS", check_run=True, labels=["tier:T2"], comments=[RISK_COMMENT.replace("37", "41")]
    )
    fake.issues["is:open"].append(_issue_node(6, ["spec-draft"]))
    report = board.sync(fake)
    updates = [v for q, v in fake.mutations if "updateProjectV2ItemFieldValue" in q]
    adds = [v for q, v in fake.mutations if "addProjectV2ItemById" in q]
    existing = [v for v in updates if v["item"] != "PVTI_new_I_6"]
    assert existing == [
        {
            "project": "PVT_1",
            "item": "PVTI_2",
            "field": "F-status",
            "value": {"singleSelectOptionId": "S-3"},
        }
    ]
    assert adds == [{"project": "PVT_1", "content": "I_6"}]
    # the new card gets its column in the same run, and nothing else: its other fields are empty
    assert [v for v in updates if v["item"] == "PVTI_new_I_6"] == [
        {
            "project": "PVT_1",
            "item": "PVTI_new_I_6",
            "field": "F-status",
            "value": {"singleSelectOptionId": "S-0"},
        }
    ]
    assert (report.seen, report.added, dict(report.changed)) == (5, 1, {"Status": 2})


def test_a_dragged_card_is_moved_back_and_gone_values_are_cleared():
    fake = _current_board()
    fake.items[0] = _item(
        "PVTI_1",
        1,
        status="In review",
        points=8,
        module="leads",
        forecast_tier="T2",
        tier="T3",
        risk=90,
    )
    report = board.sync(fake)
    sent = [
        (name, v["field"], v.get("value"))
        for name, (_, v) in zip(_mutation_names(fake), fake.mutations, strict=True)
    ]
    assert sent == [
        ("updateProjectV2ItemFieldValue", "F-status", {"singleSelectOptionId": "S-1"}),
        ("updateProjectV2ItemFieldValue", "F-points", {"number": 3.0}),
        ("updateProjectV2ItemFieldValue", "F-module", {"singleSelectOptionId": "M-2"}),
        ("clearProjectV2ItemFieldValue", "F-tier", None),
        ("clearProjectV2ItemFieldValue", "F-risk", None),
    ]
    assert dict(report.changed) == {"Status": 1, "Points": 1, "Module": 1, "Tier": 1, "Risk": 1}


def test_dry_run_prints_the_changes_and_sends_no_mutation(capsys):
    fake = _current_board()
    fake.items[3] = _item("PVTI_4", 4, status="In progress")
    fake.issues["is:open"].append(_issue_node(6, READY))
    assert main(["sync", "--dry-run"], client=fake) == 0
    assert fake.mutations == []
    out = capsys.readouterr().out
    assert "Would set #4 Status: In progress → Backlog." in out
    assert "Would add #6 to the board." in out
    assert "Would set #6 Module: (empty) → pipeline." in out


def test_closed_issues_off_the_board_are_not_added_and_not_planned_cards_are_left_alone():
    fake = _current_board()
    fake.items.append(_item("PVTI_5", 5, status="In progress", points=1))
    fake.issues["is:closed"].append(_issue_node(7, READY, state="CLOSED", reason="COMPLETED"))
    board.sync(fake)
    assert fake.mutations == []


def test_an_unknown_module_option_is_skipped(capsys):
    fake = _current_board()
    fake.issues["is:open"][0]["labels"]["nodes"][0] = {"name": "module:billing-v9"}
    board.sync(fake)
    assert fake.mutations == []  # left as it is, not cleared
    assert 'Module has no "billing-v9" option; skipped.' in capsys.readouterr().out


def test_sync_reads_every_page_with_one_query_per_page():
    fake = _current_board()
    board.sync(fake)
    queries = [q for q, _ in fake.calls]
    assert sum("items(" in q for q in queries) == 6  # six items, one per page
    assert sum("search(" in q for q in queries) == 3 + 2


def test_sync_never_sends_a_forbidden_mutation():
    scenarios = []
    for change in range(4):
        fake = _current_board()
        if change == 1:
            fake.items = []
        elif change == 2:
            fake.items[1] = _item("PVTI_2", 2)
        elif change == 3:
            fake.issues["is:open"] = []
        board.sync(fake)
        scenarios.append(fake)
    names = {name for fake in scenarios for name in _mutation_names(fake)}
    assert names and names <= SYNC_MUTATIONS
    items = {v.get("item") for fake in scenarios for _, v in fake.mutations}
    assert "PVTI_pr" not in items and "PVTI_draft" not in items
    source = Path(board.__file__).read_text()
    for forbidden in NEVER:
        assert forbidden not in source


def test_the_board_workflow_runs_only_the_sync():
    path = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "board.yml"
    workflow = yaml.safe_load(path.read_text())
    triggers = workflow[True]  # YAML 1.1 reads the bare key "on" as true
    assert triggers["schedule"] == [{"cron": "*/10 * * * *"}]
    assert "workflow_dispatch" in triggers
    assert workflow["concurrency"]["group"] == "board"
    steps = [step for job in workflow["jobs"].values() for step in job["steps"]]
    runs = [step["run"].strip() for step in steps if "run" in step]
    assert runs[-1] == "python -m sdlc.board sync"
    assert all(run.startswith("pip install") for run in runs[:-1])
    text = path.read_text()
    assert "ANTHROPIC" not in text and "claude-code" not in text and "API_KEY" not in text
    token = next(s for s in steps if s.get("uses", "").startswith("actions/create-github-app"))
    assert token["with"]["owner"] == "pragmattie"
