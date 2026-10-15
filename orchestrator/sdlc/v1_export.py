"""Load and validate v1's export of its decisions about this repository.

v1 writes the file with ``python -m sdlc.export_rebuild --out FILE``; this repository never reads
v1's code or database. ``load_export`` refuses, with ``ExportError``, a file that isn't JSON, is
for another repository, or has a decision that isn't an object, lacks a field or has a bad time.
Times are naive UTC. Nothing here touches the database, so the comparison
(``sdlc.compare_v1``) and the import (7.3) can both use it.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sdlc.config import Settings

DECISION_FIELDS = (
    "id",
    "created_at",
    "agent",
    "agent_version",
    "model_id",
    "prompt_version",
    "prompt_hash",
    "subject_type",
    "subject_id",
    "head_sha",
    "attempt",
    "trigger",
    "inputs_digest",
    "raw_score",
    "adjustment",
    "final_score",
    "tier",
    "signals",
    "output",
    "action_taken",
    "status",
    "error",
    "latency_ms",
    "input_tokens",
    "output_tokens",
    "human_override",
    "supersedes_id",
)
APPROVAL_FIELDS = (
    "pr",
    "approver_id",
    "tier",
    "status",
    "reason",
    "note",
    "requested_at",
    "decided_at",
)


class ExportError(ValueError):
    """The export can't be used; the message says why."""


@dataclass(frozen=True)
class Decision:
    id: int
    created_at: datetime
    agent: str
    agent_version: str
    model_id: str | None
    prompt_version: str | None
    prompt_hash: str | None
    subject_type: str
    subject_id: int
    head_sha: str | None
    attempt: int
    trigger: str
    inputs_digest: dict | None
    raw_score: int | None
    adjustment: int | None
    final_score: int | None
    tier: str | None
    signals: dict | None
    output: dict | None
    action_taken: dict | None
    status: str
    error: str | None
    latency_ms: int | None
    input_tokens: int | None
    output_tokens: int | None
    human_override: dict | None
    supersedes_id: int | None


@dataclass(frozen=True)
class Approval:
    pr: int
    approver_id: str | None
    tier: str | None
    status: str | None
    reason: str | None
    note: str | None
    requested_at: datetime | None
    decided_at: datetime | None


@dataclass(frozen=True)
class Export:
    exported_at: datetime
    repository: str
    decisions: tuple[Decision, ...]
    approvals: tuple[Approval, ...]


def parse_time(value: Any) -> datetime | None:
    """An ISO time as naive UTC; an offset, if given, is converted first."""
    if value is None:
        return None
    moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


def _time(value: Any, what: str, name: str) -> datetime | None:
    try:
        return parse_time(value)
    except (TypeError, ValueError) as error:
        raise ExportError(f"{what} has a bad time in {name!r}: {value!r}.") from error


def _object(item: Any, what: str) -> dict:
    if not isinstance(item, dict):
        raise ExportError(f"{what} isn't a JSON object.")
    return item


def _decision(item: Any, index: int) -> Decision:
    item = _object(item, f"Decision {index + 1} in the file")
    what = f"Decision {item.get('id', '(no id)')}"
    missing = [name for name in DECISION_FIELDS if name not in item]
    if missing:
        raise ExportError(f"{what} is missing the field {missing[0]!r}.")
    fields = {name: item[name] for name in DECISION_FIELDS}
    fields["created_at"] = _time(item["created_at"], what, "created_at")
    return Decision(**fields)


def _approval(item: Any, index: int) -> Approval:
    item = _object(item, f"Approval {index + 1} in the file")
    what = f"The approval for PR {item.get('pr')}"
    fields = {name: item.get(name) for name in APPROVAL_FIELDS}
    fields["requested_at"] = _time(fields["requested_at"], what, "requested_at")
    fields["decided_at"] = _time(fields["decided_at"], what, "decided_at")
    return Approval(**fields)


def load_export(path: str | Path, repository: str | None = None) -> Export:
    """The export at ``path``, checked against ``repository`` (``GITHUB_REPO`` by default)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as error:
        raise ExportError(f"Can't read the export {path}: {error.strerror or error}.") from error
    except ValueError as error:  # malformed JSON, or not UTF-8
        raise ExportError(f"The export {path} isn't valid JSON: {error}.") from error
    data = _object(data, "The export")
    expected = Settings().github_repo if repository is None else repository
    if data.get("repository") != expected:
        raise ExportError(f"This export is for {data.get('repository')}, not {expected}.")
    return Export(
        exported_at=_time(data.get("exported_at"), "The export", "exported_at"),
        repository=data["repository"],
        decisions=tuple(_decision(item, k) for k, item in enumerate(data.get("decisions") or [])),
        approvals=tuple(_approval(item, k) for k, item in enumerate(data.get("approvals") or [])),
    )
