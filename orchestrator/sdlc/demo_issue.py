"""Open the live demo's issue, fully set up, with one command.

The demo changes are Markdown files in ``orchestrator/demo_issues/``, one per change: the first
line is ``# <title>``, the rest is the issue body. Each change can be used once; it is used when
an issue (open or closed) is titled exactly ``Demo: <title>``.

``python -m sdlc.demo_issue list`` shows each change, fresh or used. ``python -m sdlc.demo_issue
open NAME`` creates the issue with the M8 milestone and the labels that start the builder at T1
at once; ``--dry-run`` prints it and creates nothing. The title carries no plan item number, so a
demo issue never takes a place in the rebuild plan.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sdlc.github_client import GitHubClient, GitHubError

DEMO_ISSUES_PATH = Path(__file__).resolve().parents[1] / "demo_issues"
TITLE_PREFIX = "Demo: "
MILESTONE_PREFIX = "M8 "
LABELS = ["demo", "forecast:T1", "agent-ready"]
NEXT_STEPS = (
    "Triage labels it within a minute.",
    "The builder starts now, on the agent-ready label (about 10-15 minutes).",
)


class Refusal(Exception):
    pass


@dataclass
class Change:
    name: str
    title: str
    body: str

    @property
    def issue_title(self) -> str:
        return f"{TITLE_PREFIX}{self.title}"


def names(folder: Path = DEMO_ISSUES_PATH) -> list[str]:
    return sorted(path.stem for path in folder.glob("*.md"))


def load(name: str, folder: Path = DEMO_ISSUES_PATH) -> Change:
    path = folder / f"{name}.md"
    if name not in names(folder):
        known = ", ".join(names(folder)) or "none"
        raise Refusal(f"No demo change named {name!r}. There are: {known}.")
    first, _, rest = path.read_text(encoding="utf-8").partition("\n")
    title = first.removeprefix("# ").strip() if first.startswith("# ") else ""
    if not title:
        raise Refusal(f"{path.name}: the first line must be '# <title>'.")
    return Change(name=name, title=title, body=rest.strip() + "\n")


def used_issue(gh: Any, change: Change, items: list[dict] | None = None) -> dict | None:
    """The newest issue (not pull request) titled ``Demo: <title>``, open or closed."""
    if items is None:
        items = gh.paginate("/repos/{repo}/issues", state="all")
    matches = [
        item
        for item in items
        if "pull_request" not in item and item.get("title") == change.issue_title
    ]
    return max(matches, key=lambda item: item["number"], default=None)


def m8_milestone(gh: Any) -> dict:
    for milestone in gh.paginate("/repos/{repo}/milestones", state="open"):
        if milestone["title"].startswith(MILESTONE_PREFIX):
            return milestone
    raise Refusal(f"No open milestone whose title starts with {MILESTONE_PREFIX.strip()!r}.")


def list_changes(gh: Any, folder: Path = DEMO_ISSUES_PATH) -> None:
    changes = [load(name, folder) for name in names(folder)]
    items = gh.paginate("/repos/{repo}/issues", state="all")
    width = max((len(change.name) for change in changes), default=0)
    for change in changes:
        issue = used_issue(gh, change, items)
        status = f"used: #{issue['number']} ({issue['state']})" if issue else "fresh"
        print(f"{change.name:<{width}}  {status:<20}  {change.title}")


def open_change(gh: Any, name: str, *, dry_run: bool, folder: Path = DEMO_ISSUES_PATH) -> None:
    change = load(name, folder)
    issue = used_issue(gh, change)
    if issue:
        raise Refusal(
            f"{name} is already used: #{issue['number']} ({issue['state']}). "
            "Run `list` to see the fresh ones."
        )
    milestone = m8_milestone(gh)
    print(f"Title:     {change.issue_title}")
    print(f"Milestone: {milestone['title']}")
    print(f"Labels:    {', '.join(LABELS)}")
    if dry_run:
        print("Body:")
        print(change.body, end="")
        print("Dry run: nothing was created.")
        return
    created = gh.post(
        "/repos/{repo}/issues",
        {
            "title": change.issue_title,
            "body": change.body,
            "milestone": milestone["number"],
            "labels": LABELS,
        },
    )
    print(f"Created #{created['number']}: {created.get('html_url', '')}")
    for line in NEXT_STEPS:
        print(line)


def main(argv: list[str] | None = None, gh: Any = None, folder: Path = DEMO_ISSUES_PATH) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.demo_issue", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list", help="each demo change, fresh or used")
    opener = commands.add_parser("open", help="create the demo issue for one change")
    opener.add_argument("name", help="the change's file name without .md")
    opener.add_argument("--dry-run", action="store_true", help="print it; create nothing")
    args = parser.parse_args(argv)

    try:
        gh = gh or GitHubClient()
        if args.command == "list":
            list_changes(gh, folder)
        else:
            open_change(gh, args.name, dry_run=args.dry_run, folder=folder)
    except (Refusal, GitHubError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
