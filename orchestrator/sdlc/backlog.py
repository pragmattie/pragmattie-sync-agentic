"""Create the demo product backlog as GitHub issues, with the labels every agent reads.

Dry run by default: ``python -m sdlc.backlog`` lists what it would create, and
``python -m sdlc.backlog --apply`` creates it. Issues whose exact title already exists (open or
closed) and labels that already exist are skipped, so it is safe to run again.
"""

import argparse
import sys
from pathlib import Path

import yaml

from sdlc.github_client import GitHubClient, GitHubError

BACKLOG_PATH = Path(__file__).resolve().parent.parent / "backlog" / "backlog.yaml"
INTRO = "_Created from the PragMattie Sync demo backlog._"

PREFIX_COLOURS = {
    "module": "1B8A94",
    "type": "2E3F55",
    "priority": "E8B35A",
    "points": "C5CAD1",
    "epic": "6FBAC1",
}
FIXED_LABELS = {
    "caused-incident": ("B60205", "This PR caused a production incident"),
    "incident": ("D93F0B", "A production incident: the release gate holds releases touching it"),
    "demo": ("D4C5F9", "Made during a demo: the demo reset cleans it up"),
}


def read_backlog(path: Path = BACKLOG_PATH) -> dict:
    with open(path, encoding="utf-8") as file:
        return yaml.safe_load(file)


def load_backlog(path: Path = BACKLOG_PATH) -> list[dict]:
    return read_backlog(path)["issues"]


def labels_for(item: dict) -> list[str]:
    labels = [
        f"module:{item['module']}",
        f"type:{item['type']}",
        f"priority:{item['priority']}",
        f"points:{item['points']}",
    ]
    if item.get("epic"):
        labels.append(f"epic:{item['epic']}")
    return labels


def body_for(item: dict) -> str:
    parts = [INTRO]
    if item.get("epic"):
        parts.append(f"**Epic:** {item['epic']}")
    if item.get("criteria"):
        lines = "\n".join(f"- [ ] {criterion}" for criterion in item["criteria"])
        parts.append(f"## Acceptance criteria\n\n{lines}")
    return "\n\n".join(parts)


def label_request(name: str) -> dict:
    """The body that creates ``name``: fixed labels carry a description, prefixed ones don't."""
    if name in FIXED_LABELS:
        color, description = FIXED_LABELS[name]
        return {"name": name, "color": color, "description": description}
    return {"name": name, "color": PREFIX_COLOURS[name.split(":", 1)[0]]}


def plan(items: list[dict], existing_titles: set[str], existing_labels: set[str]) -> dict:
    wanted = set(FIXED_LABELS)
    for item in items:
        wanted.update(labels_for(item))
    return {
        "labels": sorted(wanted - set(existing_labels)),
        "issues": [item for item in items if item["title"] not in existing_titles],
    }


def apply(client: GitHubClient, todo: dict) -> None:
    for name in todo["labels"]:
        client.post("/repos/{repo}/labels", label_request(name))
    for item in todo["issues"]:
        issue = client.post(
            "/repos/{repo}/issues",
            {"title": item["title"], "body": body_for(item), "labels": labels_for(item)},
        )
        print(f"  #{issue['number']} {item['title']}")


def main(argv: list[str] | None = None, client: GitHubClient | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.backlog", description="Create the demo backlog as GitHub issues."
    )
    parser.add_argument("--apply", action="store_true", help="create them (default: dry run)")
    parser.add_argument("--file", type=Path, default=BACKLOG_PATH, help="backlog YAML file")
    args = parser.parse_args(argv)

    items = load_backlog(args.file)
    try:
        client = client or GitHubClient()
        titles = {issue["title"] for issue in client.paginate("/repos/{repo}/issues", state="all")}
        labels = {label["name"] for label in client.paginate("/repos/{repo}/labels")}
        todo = plan(items, titles, labels)
        print(
            f"{client.repo}: {len(todo['labels'])} labels and "
            f"{len(todo['issues'])} issues to create."
        )
        if not args.apply:
            for item in todo["issues"]:
                print(f"  {item['title']}  [{', '.join(labels_for(item))}]")
            print("Dry run only. Re-run with --apply to create them.")
            return 0
        apply(client, todo)
    except GitHubError as error:
        sys.exit(str(error))
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
