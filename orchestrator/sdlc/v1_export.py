"""Load and validate v1's export of its decisions about this repository.

v1 writes the file with ``python -m sdlc.export_rebuild --out FILE``; this repository never reads
v1's code or database. ``load_export`` refuses a file for another repository, or one whose
decisions lack a field. Times are naive UTC. Nothing here touches the database, so the comparison
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


def _decision(item: dict) -> Decision:
    missing = [name for name in DECISION_FIELDS if name not in item]
    if missing:
        raise ExportError(
            f"Decision {item.get('id', '(no id)')} is missing the field {missing[0]!r}."
        )
    fields = {name: item[name] for name in DECISION_FIELDS}
    fields["created_at"] = parse_time(item["created_at"])
    return Decision(**fields)


def _approval(item: dict) -> Approval:
    fields = {name: item.get(name) for name in APPROVAL_FIELDS}
    fields["requested_at"] = parse_time(fields["requested_at"])
    fields["decided_at"] = parse_time(fields["decided_at"])
    return Approval(**fields)


def load_export(path: str | Path, repository: str | None = None) -> Export:
    """The export at ``path``, checked against ``repository`` (``GITHUB_REPO`` by default)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    expected = Settings().github_repo if repository is None else repository
    if data.get("repository") != expected:
        raise ExportError(f"This export is for {data.get('repository')}, not {expected}.")
    return Export(
        exported_at=parse_time(data.get("exported_at")),
        repository=data["repository"],
        decisions=tuple(_decision(item) for item in data.get("decisions") or []),
        approvals=tuple(_approval(item) for item in data.get("approvals") or []),
    )
