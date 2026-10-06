"""The implementer's and reviewer's own runs, read from the records their workflows comment.

``implement.yml`` ends each run with a comment holding ``<!-- pragmattie-run {...} -->`` and
``review.yml`` with ``<!-- pragmattie-review {...} -->``. This module parses those records and
appends each run to the audit trail once. Reading the comments from GitHub is the caller's job;
callers count only comments written by ``WORKFLOW_BOT``.
"""

import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from sdlc.audit import decisions_for, record_decision

WORKFLOW_BOT = "github-actions[bot]"
AGENT_VERSION = "1"
TIERS = ("T0", "T1", "T2", "T3")
REVIEW_VERDICTS = ("approve", "request_changes")

_RUN = re.compile(r"<!--\s*pragmattie-run\s+(\{.*?\})\s*-->", re.S)
_REVIEW = re.compile(r"<!--\s*pragmattie-review\s+(\{.*?\})\s*-->", re.S)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _parse(pattern: re.Pattern, body: str | None, subject: str) -> dict | None:
    match = pattern.search(body or "")
    if not match:
        return None
    try:
        record = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    if not isinstance(record, dict) or not _is_int(record.get(subject)) or not record.get("run"):
        return None
    return record


def parse_run(body: str | None) -> dict | None:
    """The implementer's run record in ``body``, or None when it is missing or invalid."""
    return _parse(_RUN, body, "issue")


def parse_review(body: str | None) -> dict | None:
    """The reviewer's run record in ``body``, or None when it is missing or invalid."""
    return _parse(_REVIEW, body, "pr")


def _tokens(record: dict, key: str) -> int | None:
    value = record.get(key)
    return value if _is_int(value) else None


def _commented_at(comment: Mapping) -> datetime | None:
    created_at = comment.get("created_at")
    if not created_at:
        return None
    return datetime.fromisoformat(created_at).astimezone(UTC).replace(tzinfo=None)


def _record(
    db: Session,
    record: dict,
    comment: Mapping,
    source: str,
    *,
    agent: str,
    subject_type: str,
    subject_id: int,
    head_sha: str,
    **fields: Any,
) -> bool:
    subject = {"subject_type": subject_type, "subject_source": source, "subject_id": subject_id}
    if decisions_for(db, agent, head_sha=head_sha, **subject):
        return False
    record_decision(
        db,
        agent=agent,
        agent_version=AGENT_VERSION,
        trigger="workflow",
        now=_commented_at(comment),
        head_sha=head_sha,
        model_id=record.get("model") or None,
        input_tokens=_tokens(record, "input_tokens"),
        output_tokens=_tokens(record, "output_tokens"),
        output=record,
        **subject,
        **fields,
    )
    return True


def record_run(db: Session, record: dict, comment: Mapping, source: str) -> bool:
    """Append an implementer run once; return whether a row was added. The caller commits."""
    outcome = record.get("outcome")
    ok = outcome == "success" and record.get("tests_passed") != "false"
    if ok:
        error = None
    elif record.get("error"):
        error = record["error"]
    elif outcome == "success":
        error = "Tests failed."
    else:
        error = f"Outcome {outcome!r}."
    tier = record.get("tier")
    return _record(
        db,
        record,
        comment,
        source,
        agent="implementer",
        subject_type="issue",
        subject_id=record["issue"],
        head_sha=f"run-{record['run']}",
        tier=tier if tier in TIERS else None,
        action_taken={
            "action": record.get("action"),
            "pr": record.get("pr"),
            "comment": comment.get("html_url"),
        },
        status="ok" if ok else "error",
        error=error[:500] if error else None,
    )


def record_review(db: Session, record: dict, comment: Mapping, source: str) -> bool:
    """Append a reviewer run once; return whether a row was added. The caller commits."""
    verdict = record.get("verdict")
    ok = verdict in REVIEW_VERDICTS
    error = None if ok else (record.get("error") or f"Verdict {verdict!r}.")
    return _record(
        db,
        record,
        comment,
        source,
        agent="reviewer",
        subject_type="pr",
        subject_id=record["pr"],
        head_sha=f"review-{record['run']}",
        action_taken={
            "verdict": verdict,
            "commit": record.get("sha"),
            "comment": comment.get("html_url"),
        },
        status="ok" if ok else "error",
        error=error[:500] if error else None,
    )
