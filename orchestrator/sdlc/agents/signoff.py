"""Where a pull request's sign-off comes from: the risk comment's boxes, a person's GitHub review,
the AI reviewer's approval, or the objection window passing with no ``/hold``.

Pure functions over data the caller fetches. Reviews and comments are GitHub API payloads, oldest
first, as GitHub lists them. Every source counts only for the commit it was given on.
"""

import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sdlc.agents.gate import DESCRIPTION_LIMIT, Gate
from sdlc.audit import TRIAL
from sdlc.tables import AgentDecision, PullRequest
from sdlc.tiers import Policy

COMMENT_MARKER = "<!-- pragmattie-risk-gate -->"
HEAD_MARKER = "<!-- head:{sha} -->"
SIGNOFF_BOX = re.compile(r"- \[[xX]\] \*\*Human sign-off")
QA_BOX = re.compile(r"- \[[xX]\] \*\*Manual QA done")

REVIEW_STATES = ("APPROVED", "CHANGES_REQUESTED", "DISMISSED")  # COMMENTED and PENDING don't count
WINDOW_AGENT = "objection_window"
WINDOW_AGENT_VERSION = "v1"


def _is_person(item: Mapping) -> bool:
    return (item.get("user") or {}).get("type") != "Bot"


def ticked(comment_body: str | None, sha: str) -> tuple[bool, bool]:
    """Whether the sign-off and QA boxes are ticked in the risk comment written for ``sha``."""
    body = comment_body or ""
    if not body.startswith(COMMENT_MARKER) or HEAD_MARKER.format(sha=sha) not in body:
        return False, False
    return bool(SIGNOFF_BOX.search(body)), bool(QA_BOX.search(body))


def approved_on_github(reviews: Iterable[Mapping], sha: str) -> bool:
    """Whether a person's latest review of this exact commit approves it."""
    latest: dict[str, str] = {}
    for review in reviews:
        if review.get("commit_id") != sha or not _is_person(review):
            continue
        if review.get("state") in REVIEW_STATES:
            latest[review["user"]["login"]] = review["state"]
    return "APPROVED" in latest.values()


def ai_approval(reviewer_rows: Iterable[AgentDecision], sha: str) -> AgentDecision | None:
    """The reviewer's newest verdict on this commit, if it approves.

    4.4 stores ``head_sha`` as the run, so the commit is read from ``action_taken``. Failed and
    trial runs gave no verdict and are skipped.
    """
    rows = [
        row
        for row in reviewer_rows
        if row.agent == "reviewer"
        and row.status == "ok"
        and row.trigger != TRIAL
        and (row.action_taken or {}).get("commit") == sha
    ]
    if not rows:
        return None
    newest = max(rows, key=lambda row: (row.created_at, row.id))
    return newest if newest.action_taken.get("verdict") == "approve" else None


def objection_window(
    policy: Policy,
    tier: str,
    changed_paths: Iterable[str],
    ai_approved_at: datetime | None,
    comments: Iterable[Mapping],
) -> datetime | None:
    """When the reviewer's approval counts as the sign-off, or None if it never will."""
    window = policy.objection_window
    if window is None or ai_approved_at is None or tier not in window.tiers:
        return None
    paths = list(changed_paths)
    if not paths or not all(path.startswith(window.paths) for path in paths):
        return None
    for comment in comments:
        body = (comment.get("body") or "").lstrip().lower()
        if body.startswith("/hold") and _is_person(comment):
            return None
    return ai_approved_at + timedelta(minutes=window.minutes)


def window_signoff_fields(
    pr: PullRequest, sha: str, tier: str, approval: AgentDecision, policy: Policy
) -> dict[str, Any]:
    """The ``record_decision`` arguments for a sign-off given by the window. Write once a commit."""
    window = policy.objection_window
    return {
        "agent": WINDOW_AGENT,
        "agent_version": WINDOW_AGENT_VERSION,
        "subject_type": "pr",
        "subject_source": pr.source,
        "subject_id": pr.number,
        "trigger": "schedule",
        "head_sha": f"window-{sha}",
        "tier": tier,
        "output": {
            "commit": sha,
            "reviewer_approval": approval.id,
            "approved_at": approval.created_at.isoformat(),
            "minutes": window.minutes if window else None,
            "paths": list(window.paths) if window else [],
        },
        "action_taken": {"signoff": "objection window passed with no /hold"},
    }


def describe_window(gate: Gate, merges_at: datetime) -> str:
    """The gate's description, saying when a pending pull request merges unless held."""
    if gate.would_be != "pending":
        return gate.description
    if merges_at.tzinfo is not None:
        merges_at = merges_at.astimezone(UTC)
    suffix = f" or, unless someone comments /hold, merges after {merges_at:%H:%M} UTC"
    return (gate.description + suffix)[:DESCRIPTION_LIMIT]
