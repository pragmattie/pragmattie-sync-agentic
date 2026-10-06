"""The stored ``risk-gate`` state: one row a pull request, replaced each time the gate is posted.

A read model, not history: ``upsert`` overwrites the PR's row. It flushes but never commits; the
caller commits it together with the status it posts.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc.agents.gate import Gate
from sdlc.clock import utcnow
from sdlc.tables import GateStatus, PullRequest


def upsert(
    db: Session,
    pr: PullRequest,
    gate: Gate,
    *,
    tier: str,
    mode: str,
    now: datetime | None = None,
) -> GateStatus:
    row = db.scalar(select(GateStatus).where(GateStatus.pull_request_id == pr.id))
    if row is None:
        row = GateStatus(pull_request_id=pr.id)
        db.add(row)
    row.tier = tier
    row.state = gate.state
    row.would_be = gate.would_be
    row.missing = list(gate.missing)
    row.description = gate.description
    row.mode = mode
    row.updated_at = (now or utcnow()).replace(microsecond=0)
    db.flush()
    return row
