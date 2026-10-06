"""The triage agent's comment: what it decided about an issue and why, kept as one comment.

Everything here is rendered by code from the assessment. These are pure functions with no I/O:
the caller posts the comment. ``content_version`` names the version of an issue's text a
decision was made for; labels and comments don't change it, so the agent's own writes can never
trigger another run.
"""

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from sdlc.agents.comment import footer
from sdlc.agents.triage import Assessment

MARKER = "<!-- pragmattie-triage -->"
VERSION_MARKER = "<!-- version:{version} -->"
VERSION_CHARS = 12
SIMILAR_SHOWN = 5

_VERSION = re.compile(r"<!-- version:([0-9a-f]+) -->")


def content_version(title: str | None, body: str | None) -> str:
    """The first 12 hex characters of the sha256 of the title and body."""
    text = f"{title or ''}\n{body or ''}"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:VERSION_CHARS]


def comment_version(body: str | None) -> str | None:
    """The version of the issue's text a comment was written for, or None."""
    match = _VERSION.search(body or "")
    return match.group(1) if match else None


@dataclass(frozen=True)
class Meta:
    decision_id: int | None
    version: str
    overrides: Sequence[str] = ()  # the dimensions a person has already set


def _similar_block(assessment: Assessment) -> str:
    lines = [
        f"- #{issue.number} {issue.title} ({issue.state})"
        for issue in assessment.similar[:SIMILAR_SHOWN]
    ]
    return "\n".join(
        ["<details><summary>Similar past issues</summary>", "", *lines, "", "</details>"]
    )


def render(assessment: Assessment, meta: Meta) -> str:
    a = assessment
    head = [MARKER, VERSION_MARKER.format(version=meta.version)]
    if not a.ok:
        sections = [
            "\n".join([*head, "## Triage"]),
            f"**Could not triage this issue yet** ({a.status}). It will try again shortly.",
        ]
    else:
        confidence = round((a.confidence or 0) * 100)
        sections = [
            "\n".join([*head, f"## Triage: {a.module} / {a.type} ({a.priority})"]),
            f"**Estimate {a.estimate_points} points**, confidence {confidence}%.",
        ]
        if a.rationale:
            sections.append(a.rationale)
        if a.duplicate_of:
            sections.append(f"Possibly a duplicate of #{a.duplicate_of} — linked, not closed.")
        if a.questions:
            questions = [f"- {question}" for question in a.questions]
            sections.append("\n".join(["**Before this is ready to work on:**", *questions]))
        if meta.overrides:
            sections.append(f"_A human has already set {', '.join(meta.overrides)}; left as-is._")
        if a.similar:
            sections.append(_similar_block(a))
    sections.append(footer(a, meta.decision_id))
    return "\n\n".join(sections) + "\n"
