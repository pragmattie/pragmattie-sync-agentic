"""The only place the orchestrator writes to GitHub.

Reads always work. Every write goes through ``_write``, which does nothing in ``off`` mode and
returns what happened for the audit row: ``{"done": ...}``, ``{"skipped": "mode is off"}`` or
``{"error": ...}``. A failing write never raises, so one never stops the others.

There is deliberately no method that merges, approves, closes, pushes, edits an issue's text or
changes repository settings.
"""

from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import quote

from sdlc.agents.gate import DESCRIPTION_LIMIT

TIER_COLOURS = {"T0": "0E8A16", "T1": "FBCA04", "T2": "F9A03F", "T3": "B60205"}
DIMENSION_COLOUR = "C5DEF5"
ERROR_CHARS = 300


class Effects:
    def __init__(self, gh: Any, mode: str):
        self.gh = gh
        self.mode = mode

    # Reads

    def read_pr(self, number: int) -> dict:
        return self.gh.get(f"/repos/{{repo}}/pulls/{number}")

    def read_diff(self, number: int) -> str:
        return self.gh.get_text(f"/repos/{{repo}}/pulls/{number}")

    def read_files(self, number: int) -> list[dict]:
        return self.gh.paginate(f"/repos/{{repo}}/pulls/{number}/files")

    def read_comments(self, number: int) -> list[dict]:
        return self.gh.paginate(f"/repos/{{repo}}/issues/{number}/comments")

    def find_comment(
        self, number: int, marker: str, comments: list[Mapping] | None = None
    ) -> Mapping | None:
        """The first comment by a bot starting with ``marker``; a person's copy never counts."""
        if comments is None:
            comments = self.read_comments(number)
        for comment in comments:
            user = comment.get("user") or {}
            if user.get("type") == "Bot" and (comment.get("body") or "").startswith(marker):
                return comment
        return None

    def read_issue(self, number: int) -> dict:
        return self.gh.get(f"/repos/{{repo}}/issues/{number}")

    def read_labels(self, number: int) -> list[str]:
        labels = self.gh.paginate(f"/repos/{{repo}}/issues/{number}/labels")
        return [label["name"] for label in labels]

    def read_reviews(self, number: int) -> list[dict]:
        return self.gh.paginate(f"/repos/{{repo}}/pulls/{number}/reviews")

    # Writes

    def _write(self, done: str, action: Callable[[], Any]) -> dict[str, str]:
        if self.mode == "off":
            return {"skipped": "mode is off"}
        try:
            action()
        except Exception as error:  # a failed write is reported, never raised
            return {"error": f"{done}: {error}"[:ERROR_CHARS]}
        return {"done": done}

    def upsert_comment(
        self, number: int, marker: str, body: str, comments: list[Mapping] | None = None
    ) -> dict[str, str]:
        """Edit the bot's comment starting with ``marker``, or add it."""

        def action() -> None:
            existing = self.find_comment(number, marker, comments)
            if existing:
                self.gh.patch(f"/repos/{{repo}}/issues/comments/{existing['id']}", {"body": body})
            else:
                self.gh.post(f"/repos/{{repo}}/issues/{number}/comments", {"body": body})

        return self._write("comment", action)

    def set_status(
        self, sha: str, state: str, description: str, context: str = "risk-gate"
    ) -> dict[str, str]:
        body = {"state": state, "description": description[:DESCRIPTION_LIMIT], "context": context}
        return self._write(
            f"{context} {state}", lambda: self.gh.post(f"/repos/{{repo}}/statuses/{sha}", body)
        )

    def set_tier_label(self, number: int, tier: str) -> dict[str, str]:
        return self._write(
            f"tier:{tier}", lambda: self._set_prefixed(number, "tier", tier, TIER_COLOURS[tier])
        )

    def set_dimension_label(
        self, number: int, key: str, value: str, colour: str = DIMENSION_COLOUR
    ) -> dict[str, str]:
        """Set ``key:value``, removing any other ``key:`` label."""
        return self._write(f"{key}:{value}", lambda: self._set_prefixed(number, key, value, colour))

    def add_label_if_absent(
        self, number: int, name: str, colour: str = DIMENSION_COLOUR
    ) -> dict[str, str]:
        def action() -> None:
            if name not in self.read_labels(number):
                self._add(number, name, colour)

        return self._write(f"add {name}", action)

    def remove_label(self, number: int, name: str) -> dict[str, str]:
        def action() -> None:
            if name in self.read_labels(number):
                self._remove(number, name)

        return self._write(f"remove {name}", action)

    def _set_prefixed(self, number: int, key: str, value: str, colour: str) -> None:
        wanted = f"{key}:{value}"
        current = self.read_labels(number)
        for name in current:
            if name != wanted and name.partition(":")[0].strip() == key:
                self._remove(number, name)
        if wanted not in current:
            self._add(number, wanted, colour)

    def _add(self, number: int, name: str, colour: str) -> None:
        existing = {label["name"] for label in self.gh.paginate("/repos/{repo}/labels")}
        if name not in existing:
            self.gh.post("/repos/{repo}/labels", {"name": name, "color": colour})
        self.gh.post(f"/repos/{{repo}}/issues/{number}/labels", {"labels": [name]})

    def _remove(self, number: int, name: str) -> None:
        self.gh.delete(f"/repos/{{repo}}/issues/{number}/labels/{quote(name, safe='')}")
