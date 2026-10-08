"""The delivery board (a GitHub Projects board): check its statuses and fields, and keep it current.

The board already exists: this adopts it and never creates a new one. ``python -m sdlc.board
setup`` lists what is missing or wrong and changes nothing; ``setup --apply`` also creates the
missing fields, with their options. Nothing here deletes or renames a field or an option, deletes
an item, or replaces an existing field's options: GitHub gives replaced options new ids, which
would clear the status of every card on the board. A problem with an existing field is reported
with where to fix it in GitHub, and ``setup`` exits 1.

``python -m sdlc.board sync`` keeps every issue's card current. The board is computed, not edited
by people: each run works out every card's Status, Points, Module, Forecast tier, Tier and Risk
from GitHub alone (labels, the pull requests that close the issue, their ``risk-gate`` status and
risk comment) and sets each value that differs, so a card dragged by hand is moved back on the
next run. It adds open issues that aren't on the board, and never removes or archives an item,
touches a pull-request item or edits an issue. It writes no audit rows and needs no database.

The token needs Organization → Projects: read and write.
"""

import argparse
import re
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import timedelta

from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.github_client import GitHubClient, GitHubError
from sdlc.tables import MODULES

STATUS = "Status"
STATUSES = ("Backlog", "Triaged", "In progress", "In review", "Gated", "Merged", "Production")
NUMBER = "NUMBER"
SINGLE_SELECT = "SINGLE_SELECT"
TIERS = ("T0", "T1", "T2", "T3")
FIELDS = {
    "Points": (NUMBER, ()),
    "Module": (SINGLE_SELECT, MODULES),
    "Forecast tier": (SINGLE_SELECT, TIERS),
    "Tier": (SINGLE_SELECT, TIERS),
    "Risk": (NUMBER, ()),
}
TYPE_NAMES = {NUMBER: "number", SINGLE_SELECT: "single select"}

VIEW_LAYOUT = (
    "Views can't be set through GitHub's API. Check the board has this view by hand:\n"
    '  View "Delivery", Board layout, columns by Status, swimlanes by Milestone,\n'
    "  fields Points, Module, Forecast tier, Tier and Risk shown."
)

BOARD_QUERY = """
query($owner: String!, $number: Int!) {
  organization(login: $owner) {
    projectV2(number: $number) {
      id
      title
      fields(first: 50) {
        nodes {
          ... on ProjectV2FieldCommon { id name dataType }
          ... on ProjectV2SingleSelectField { options { id name } }
        }
      }
    }
  }
}
"""

CREATE_FIELD = """
mutation($input: CreateProjectV2FieldInput!) {
  createProjectV2Field(input: $input) {
    projectV2Field { ... on ProjectV2FieldCommon { id name } }
  }
}
"""


@dataclass
class Field:
    id: str
    name: str
    data_type: str
    options: dict[str, str] = field(default_factory=dict)  # option name -> id, in board order


@dataclass
class Board:
    owner: str
    number: int
    project_id: str
    title: str
    status_field_id: str | None
    statuses: dict[str, str]  # Status option name -> id, in board order
    fields: dict[str, Field]  # every field by name, Status included

    @property
    def settings_url(self) -> str:
        return f"https://github.com/orgs/{self.owner}/projects/{self.number}/settings"


@dataclass
class Problem:
    kind: str  # missing_status, status_order, missing_field, wrong_type or missing_option
    message: str
    fix: str
    field_name: str

    @property
    def creatable(self) -> bool:
        """Whether ``setup --apply`` fixes it; every other problem needs a person."""
        return self.kind == "missing_field"


def load_board(gh: GitHubClient, owner: str | None = None, number: int | None = None) -> Board:
    settings = get_settings()
    owner = owner or settings.board_owner
    number = number or settings.board_number
    data = gh.graphql(BOARD_QUERY, owner=owner, number=number)
    project = (data.get("organization") or {}).get("projectV2")
    if not project:
        raise GitHubError(f"No project {number} in the {owner} organization.")
    fields = {}
    for node in project["fields"]["nodes"]:
        if not node.get("id"):
            continue
        options = {option["name"]: option["id"] for option in node.get("options") or []}
        fields[node["name"]] = Field(node["id"], node["name"], node["dataType"], options)
    status = fields.get(STATUS)
    return Board(
        owner=owner,
        number=number,
        project_id=project["id"],
        title=project.get("title", ""),
        status_field_id=status.id if status else None,
        statuses=dict(status.options) if status else {},
        fields=fields,
    )


def check(board: Board) -> list[Problem]:
    where = f"{board.settings_url} → Fields"
    problems = []
    for name in STATUSES:
        if name not in board.statuses:
            problems.append(
                Problem(
                    "missing_status",
                    f'Status has no "{name}" option.',
                    f'{where} → Status → Add option "{name}", then drag it into place.',
                    STATUS,
                )
            )
    present = [name for name in board.statuses if name in STATUSES]
    expected = [name for name in STATUSES if name in board.statuses]
    if present != expected:
        problems.append(
            Problem(
                "status_order",
                f"Status options are out of order: {', '.join(present)}.",
                f"{where} → Status → drag the options into this order: {', '.join(STATUSES)}.",
                STATUS,
            )
        )
    for name, (data_type, options) in FIELDS.items():
        found = board.fields.get(name)
        if found is None:
            problems.append(
                Problem(
                    "missing_field",
                    f"The {name} field is missing.",
                    f"python -m sdlc.board setup --apply creates it ({describe(name)}).",
                    name,
                )
            )
        elif found.data_type != data_type:
            actual, wanted = type_name(found.data_type), type_name(data_type)
            problems.append(
                Problem(
                    "wrong_type",
                    f"The {name} field is {actual}, not {wanted}.",
                    f'{where} → {name} → rename it (e.g. "{name} (old)"), then run '
                    f"python -m sdlc.board setup --apply to create the right one.",
                    name,
                )
            )
        else:
            for option in options:
                if option not in found.options:
                    problems.append(
                        Problem(
                            "missing_option",
                            f'The {name} field has no "{option}" option.',
                            f'{where} → {name} → Add option "{option}".',
                            name,
                        )
                    )
    return problems


def type_name(data_type: str) -> str:
    return TYPE_NAMES.get(data_type, data_type.lower().replace("_", " "))


def describe(name: str) -> str:
    data_type, options = FIELDS[name]
    return f"{type_name(data_type)}: {', '.join(options)}" if options else type_name(data_type)


def create_field(gh: GitHubClient, board: Board, name: str) -> None:
    data_type, options = FIELDS[name]
    field_input: dict = {"projectId": board.project_id, "dataType": data_type, "name": name}
    if options:
        field_input["singleSelectOptions"] = [
            {"name": option, "color": "GRAY", "description": ""} for option in options
        ]
    gh.graphql(CREATE_FIELD, input=field_input)


def setup(gh: GitHubClient, *, apply: bool = False) -> list[str]:
    """Print what ``check`` finds; with ``apply``, create the missing fields.

    Returns the problems left for a person, so an empty list means the board is ready.
    """
    board = load_board(gh)
    problems = check(board)
    print(f'Board "{board.title}" ({board.owner} #{board.number}): {len(problems)} problems.')
    for problem in problems:
        print(f"  {problem.message}\n    Fix: {problem.fix}")
    for_person = [problem.message for problem in problems if not problem.creatable]
    missing = [problem.field_name for problem in problems if problem.creatable]
    if for_person:
        if apply and missing:
            print("Nothing created: fix the problems above in GitHub first, then re-run.")
    elif missing and apply:
        for name in missing:
            create_field(gh, board, name)
            print(f"  Created the {name} field ({describe(name)}).")
    elif missing:
        print("Dry run only. Re-run with --apply to create the missing fields.")
    print(VIEW_LAYOUT)
    return for_person


# Keeping the board current (sync)

GATE_CONTEXT = "risk-gate"
RISK_MARKER = "<!-- pragmattie-risk-gate -->"
RISK_SCORE = re.compile(r"\*\*Score (\d+)/100\*\*")
PLAN_HEADING = re.compile(r"^## Plan\b", re.MULTILINE)
APPROVE_PLAN = "/approve-plan"
RECENTLY_CLOSED_DAYS = 14
LABEL_FIELDS = {"points:": "Points", "module:": "Module", "forecast:": "Forecast tier"}

ITEMS_QUERY = """
query($owner: String!, $number: Int!, $after: String) {
  organization(login: $owner) {
    projectV2(number: $number) {
      items(first: 100, after: $after) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id
          content { __typename ... on Issue { id number } }
          fieldValues(first: 30) {
            nodes {
              ... on ProjectV2ItemFieldNumberValue {
                number
                field { ... on ProjectV2FieldCommon { name } }
              }
              ... on ProjectV2ItemFieldSingleSelectValue {
                name
                field { ... on ProjectV2FieldCommon { name } }
              }
            }
          }
        }
      }
    }
  }
}
"""

ISSUES_QUERY = """
query($q: String!, $after: String) {
  search(type: ISSUE, query: $q, first: 50, after: $after) {
    pageInfo { hasNextPage endCursor }
    nodes {
      ... on Issue {
        id
        number
        state
        stateReason
        labels(first: 50) { nodes { name } }
        comments(last: 50) { nodes { body author { login __typename } } }
        closedByPullRequestsReferences(first: 10, includeClosedPrs: true) {
          nodes {
            number
            state
            isDraft
            merged
            labels(first: 50) { nodes { name } }
            comments(last: 50) { nodes { body } }
            commits(last: 1) {
              nodes {
                commit {
                  statusCheckRollup {
                    contexts(first: 100) {
                      nodes {
                        __typename
                        ... on StatusContext { context state }
                        ... on CheckRun { name status conclusion }
                      }
                    }
                  }
                }
              }
            }
          }
        }
      }
    }
  }
}
"""

ADD_ITEM = """
mutation($project: ID!, $content: ID!) {
  addProjectV2ItemById(input: {projectId: $project, contentId: $content}) { item { id } }
}
"""

UPDATE_VALUE = """
mutation($project: ID!, $item: ID!, $field: ID!, $value: ProjectV2FieldValue!) {
  updateProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field, value: $value}
  ) { projectV2Item { id } }
}
"""

CLEAR_VALUE = """
mutation($project: ID!, $item: ID!, $field: ID!) {
  clearProjectV2ItemFieldValue(
    input: {projectId: $project, itemId: $item, fieldId: $field}
  ) { projectV2Item { id } }
}
"""


def _label_value(labels: list[str], prefix: str) -> str | None:
    for label in labels:
        if label.startswith(prefix):
            return label[len(prefix) :].strip() or None
    return None


def awaiting_plan_approval(issue: dict) -> bool:
    """Labelled ``plan-proposed`` with no person's ``/approve-plan`` after the latest plan."""
    if "plan-proposed" not in issue["labels"]:
        return False
    comments = issue.get("comments", [])
    latest_plan = max(
        (index for index, c in enumerate(comments) if PLAN_HEADING.search(c["body"])), default=-1
    )
    return not any(
        c["body"].lstrip().startswith(APPROVE_PLAN) and not c.get("bot")
        for c in comments[latest_plan + 1 :]
    )


def pr_column(pr: dict) -> str | None:
    """The column one pull request puts its issue in; ``None`` for a closed, unmerged one."""
    if pr["merged"]:
        return "Merged"
    if pr["state"] != "OPEN":
        return None
    if pr["draft"]:
        return "In progress"
    if pr.get("gate") in ("pending", "failure"):
        return "Gated"
    if pr.get("gate") == "success":
        return "In review"
    return "In progress"  # not scored yet


def deciding_pr(prs: list[dict]) -> dict | None:
    """The most advanced pull request: merged, then gated, in review, then in progress."""
    ranked = [(STATUSES.index(column), pr) for pr in prs if (column := pr_column(pr))]
    return max(ranked, key=lambda pair: pair[0])[1] if ranked else None


def column_for(issue: dict, prs: list[dict]) -> str | None:
    """The Status column for an issue; ``None`` leaves its card alone."""
    if issue["state"] == "CLOSED" and issue.get("state_reason") in ("NOT_PLANNED", "DUPLICATE"):
        return None
    if issue["state"] == "CLOSED":
        return "Merged"
    pr = deciding_pr(prs)
    if pr is not None:
        return pr_column(pr)
    labels = issue["labels"]
    if "agent-ready" in labels:  # an agent is planning or building, unless a person must act
        return "Triaged" if awaiting_plan_approval(issue) else "In progress"
    if "spec-draft" in labels or "needs-info" in labels:
        return "Backlog"
    if _label_value(labels, "module:") is None or _label_value(labels, "points:") is None:
        return "Backlog"
    return "Triaged"


def risk_score(pr: dict) -> int | None:
    """The score in the pull request's latest risk comment, or ``None``."""
    for body in reversed(pr.get("comments", [])):
        if RISK_MARKER in body:
            match = RISK_SCORE.search(body)
            return int(match.group(1)) if match else None
    return None


def fields_for(issue: dict, prs: list[dict]) -> dict:
    """Every field but Status, by name; ``None`` means the field should be empty."""
    values: dict = {}
    for prefix, name in LABEL_FIELDS.items():
        values[name] = _label_value(issue["labels"], prefix)
    if values["Points"] is not None:
        try:
            values["Points"] = float(values["Points"])
        except ValueError:
            values["Points"] = None
    pr = deciding_pr(prs)
    values["Tier"] = _label_value(pr["labels"], "tier:") if pr else None
    values["Risk"] = risk_score(pr) if pr else None
    return values


def _gate_state(contexts: list[dict]) -> str | None:
    """The head commit's ``risk-gate`` as ``success``, ``pending`` or ``failure``."""
    for node in contexts:
        if node.get("__typename") == "StatusContext" and node.get("context") == GATE_CONTEXT:
            state = node.get("state")
            if state == "SUCCESS":
                return "success"
            return "pending" if state in ("PENDING", "EXPECTED") else "failure"
        if node.get("__typename") == "CheckRun" and node.get("name") == GATE_CONTEXT:
            if node.get("status") != "COMPLETED":
                return "pending"
            return "success" if node.get("conclusion") == "SUCCESS" else "failure"
    return None


def _names(connection: dict | None) -> list[str]:
    return [node["name"] for node in (connection or {}).get("nodes") or []]


def _parse_pr(node: dict) -> dict:
    commits = (node.get("commits") or {}).get("nodes") or []
    rollup = (commits[-1]["commit"].get("statusCheckRollup") or {}) if commits else {}
    contexts = (rollup.get("contexts") or {}).get("nodes") or []
    return {
        "number": node["number"],
        "state": node["state"],
        "draft": node.get("isDraft", False),
        "merged": node.get("merged", False),
        "gate": _gate_state(contexts),
        "labels": _names(node.get("labels")),
        "comments": [c["body"] for c in (node.get("comments") or {}).get("nodes") or []],
    }


def _parse_issue(node: dict) -> tuple[dict, list[dict]]:
    comments = []
    for comment in (node.get("comments") or {}).get("nodes") or []:
        author = comment.get("author") or {}
        comments.append({"body": comment["body"], "bot": author.get("__typename") == "Bot"})
    issue = {
        "id": node["id"],
        "number": node["number"],
        "state": node["state"],
        "state_reason": node.get("stateReason"),
        "labels": _names(node.get("labels")),
        "comments": comments,
    }
    refs = (node.get("closedByPullRequestsReferences") or {}).get("nodes") or []
    return issue, [_parse_pr(pr) for pr in refs if pr]


def _paginate(gh: GitHubClient, query: str, path: tuple[str, ...], **variables) -> list[dict]:
    nodes: list[dict] = []
    after = None
    while True:
        data = gh.graphql(query, after=after, **variables)
        for key in path:
            data = (data or {}).get(key) or {}
        nodes.extend(node for node in data.get("nodes") or [] if node)
        page = data.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return nodes
        after = page["endCursor"]


@dataclass
class Item:
    id: str
    values: dict  # field name -> number or option name


def load_items(gh: GitHubClient, board: Board) -> dict[str, Item]:
    """The board's issue items, by issue node id; pull-request and draft items are left out."""
    path = ("organization", "projectV2", "items")
    nodes = _paginate(gh, ITEMS_QUERY, path, owner=board.owner, number=board.number)
    items = {}
    for node in nodes:
        content = node.get("content") or {}
        if content.get("__typename") != "Issue":
            continue
        values = {}
        for value in (node.get("fieldValues") or {}).get("nodes") or []:
            name = ((value or {}).get("field") or {}).get("name")
            if name:
                values[name] = value["number"] if "number" in value else value.get("name")
        items[content["id"]] = Item(node["id"], values)
    return items


def load_issues(gh: GitHubClient) -> list[tuple[dict, list[dict]]]:
    """Open issues and issues closed in the last 14 days, each with its closing pull requests."""
    since = (utcnow() - timedelta(days=RECENTLY_CLOSED_DAYS)).date().isoformat()
    queries = (
        f"repo:{gh.repo} is:issue is:open",
        f"repo:{gh.repo} is:issue is:closed closed:>={since}",
    )
    return [_parse_issue(n) for q in queries for n in _paginate(gh, ISSUES_QUERY, ("search",), q=q)]


@dataclass
class Report:
    seen: int = 0
    added: int = 0
    changed: Counter = field(default_factory=Counter)  # field name -> values set or cleared

    def summary(self) -> str:
        changes = ", ".join(f"{name} {count}" for name, count in self.changed.items()) or "none"
        return f"{self.seen} items seen, {self.added} added; changed: {changes}."


def _same(current, wanted) -> bool:
    if isinstance(current, int | float) and isinstance(wanted, int | float):
        return float(current) == float(wanted)
    return current == wanted


def sync(gh: GitHubClient, *, dry_run: bool = False) -> Report:
    """Set every issue card's Status and fields from GitHub; add open issues not on the board."""
    board = load_board(gh)
    items = load_items(gh, board)
    report = Report()
    prefix = "Would " if dry_run else ""
    for issue, prs in load_issues(gh):
        status = column_for(issue, prs)
        item = items.get(issue["id"])
        if status is None or (item is None and issue["state"] != "OPEN"):
            continue
        report.seen += 1
        if item is None:
            print(f"{prefix}add #{issue['number']} to the board.")
            report.added += 1
            item = Item("", {})
            if not dry_run:
                data = gh.graphql(ADD_ITEM, project=board.project_id, content=issue["id"])
                item.id = data["addProjectV2ItemById"]["item"]["id"]
        wanted = {STATUS: status, **fields_for(issue, prs)}
        for name, value in wanted.items():
            current = item.values.get(name)
            if _same(current, value):
                continue
            found = board.fields.get(name)
            if found is None:
                print(f"  #{issue['number']}: the board has no {name} field; run setup.")
                continue
            if value is not None and found.data_type == SINGLE_SELECT:
                if value not in found.options:
                    print(f'  #{issue["number"]}: {name} has no "{value}" option; skipped.')
                    continue
            was = "(empty)" if current is None else current
            shown = "(empty)" if value is None else value
            print(f"{prefix}set #{issue['number']} {name}: {was} → {shown}.")
            report.changed[name] += 1
            if dry_run:
                continue
            variables = {"project": board.project_id, "item": item.id, "field": found.id}
            if value is None:
                gh.graphql(CLEAR_VALUE, **variables)
            elif found.data_type == SINGLE_SELECT:
                gh.graphql(
                    UPDATE_VALUE, value={"singleSelectOptionId": found.options[value]}, **variables
                )
            else:
                gh.graphql(UPDATE_VALUE, value={"number": float(value)}, **variables)
    print(report.summary())
    return report


def main(argv: list[str] | None = None, client: GitHubClient | None = None) -> int:
    # The output has arrows; a cp1252 console (Windows) can't encode them otherwise.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.board",
        description="Check the delivery board's statuses and fields, and keep its cards current.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup", help="check the board and list what is missing")
    setup_parser.add_argument(
        "--apply", action="store_true", help="create the missing fields (default: dry run)"
    )
    sync_parser = commands.add_parser("sync", help="move every card and set its fields")
    sync_parser.add_argument(
        "--dry-run", action="store_true", help="print the changes and send no mutation"
    )
    args = parser.parse_args(argv)

    try:
        gh = client or GitHubClient()
        if args.command == "sync":
            sync(gh, dry_run=args.dry_run)
            return 0
        left = setup(gh, apply=args.apply)
    except GitHubError as error:
        sys.exit(str(error))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
