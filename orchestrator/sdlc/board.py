"""Check the delivery board (a GitHub Projects board) has every status and field the agents keep.

The board already exists: this adopts it and never creates a new one. ``python -m sdlc.board
setup`` lists what is missing or wrong and changes nothing; ``setup --apply`` also creates the
missing fields, with their options. Nothing here deletes or renames a field or an option, deletes
an item, or replaces an existing field's options: GitHub gives replaced options new ids, which
would clear the status of every card on the board. A problem with an existing field is reported
with where to fix it in GitHub, and ``setup`` exits 1.

The token needs Organization → Projects: read and write.
"""

import argparse
import sys
from dataclasses import dataclass, field

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


def main(argv: list[str] | None = None, client: GitHubClient | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.board", description="Check the delivery board's statuses and fields."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    setup_parser = commands.add_parser("setup", help="check the board and list what is missing")
    setup_parser.add_argument(
        "--apply", action="store_true", help="create the missing fields (default: dry run)"
    )
    args = parser.parse_args(argv)

    try:
        left = setup(client or GitHubClient(), apply=args.apply)
    except GitHubError as error:
        sys.exit(str(error))
    return 1 if left else 0


if __name__ == "__main__":
    sys.exit(main())
