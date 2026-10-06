"""The audit trail of agent decisions: append a row, read rows back.

Rows are only ever added. A correction is a new row whose ``supersedes_id`` points at the row it
replaces, so nothing here changes or removes a row. ``record_decision`` flushes but never commits:
the caller commits the row together with the action it records.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from sdlc.clock import utcnow
from sdlc.tables import AgentDecision

TRIAL = "trial"


def record_decision(
    db: Session,
    *,
    agent: str,
    agent_version: str,
    subject_type: str,
    subject_source: str,
    subject_id: int,
    trigger: str,
    now: datetime | None = None,
    **fields: Any,
) -> AgentDecision:
    row = AgentDecision(
        created_at=(now or utcnow()).replace(microsecond=0),
        agent=agent,
        agent_version=agent_version,
        subject_type=subject_type,
        subject_source=subject_source,
        subject_id=subject_id,
        trigger=trigger,
        **{"attempt": 1, **fields},
    )
    db.add(row)
    db.flush()
    return row


def decisions_for(
    db: Session,
    agent: str,
    *,
    subject_type: str,
    subject_source: str,
    subject_id: int,
    head_sha: str | None,
) -> list[AgentDecision]:
    """Every attempt at this version of the subject, oldest first, trial rows left out."""
    query = select(AgentDecision).where(
        AgentDecision.agent == agent,
        AgentDecision.subject_type == subject_type,
        AgentDecision.subject_source == subject_source,
        AgentDecision.subject_id == subject_id,
        AgentDecision.head_sha == head_sha,  # None compares as IS NULL
        AgentDecision.trigger != TRIAL,
    )
    return list(db.scalars(query.order_by(AgentDecision.created_at, AgentDecision.id)))


def latest_decision(
    db: Session, agent: str, *, subject_type: str, subject_source: str, subject_id: int
) -> AgentDecision | None:
    """The newest ``ok``, non-trial decision on the subject, at any version."""
    query = select(AgentDecision).where(
        AgentDecision.agent == agent,
        AgentDecision.subject_type == subject_type,
        AgentDecision.subject_source == subject_source,
        AgentDecision.subject_id == subject_id,
        AgentDecision.status == "ok",
        AgentDecision.trigger != TRIAL,
    )
    return db.scalars(
        query.order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc()).limit(1)
    ).first()


def _filtered(
    query: Select,
    *,
    agent: str | None,
    subject_type: str | None,
    subject_source: str | None,
    status: str | None,
    tier: str | None,
) -> Select:
    filters = {
        AgentDecision.agent: agent,
        AgentDecision.subject_type: subject_type,
        AgentDecision.subject_source: subject_source,
        AgentDecision.status: status,
        AgentDecision.tier: tier,
    }
    for column, value in filters.items():
        if value is not None:
            query = query.where(column == value)
    return query


def list_decisions(
    db: Session,
    *,
    agent: str | None = None,
    subject_type: str | None = None,
    subject_source: str | None = None,
    status: str | None = None,
    tier: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[AgentDecision]:
    """Decisions matching every given filter, newest first, trial rows included."""
    query = _filtered(
        select(AgentDecision),
        agent=agent,
        subject_type=subject_type,
        subject_source=subject_source,
        status=status,
        tier=tier,
    )
    query = query.order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc())
    return list(db.scalars(query.limit(limit).offset(offset)))


def count_decisions(
    db: Session,
    *,
    agent: str | None = None,
    subject_type: str | None = None,
    subject_source: str | None = None,
    status: str | None = None,
    tier: str | None = None,
) -> int:
    query = _filtered(
        select(func.count()).select_from(AgentDecision),
        agent=agent,
        subject_type=subject_type,
        subject_source=subject_source,
        status=status,
        tier=tier,
    )
    return db.scalar(query)


def serialize_decision(row: AgentDecision) -> dict[str, Any]:
    """Every column as a JSON-safe value, ``created_at`` as ISO text."""
    data = {column.key: getattr(row, column.key) for column in AgentDecision.__table__.columns}
    data["created_at"] = row.created_at.isoformat() if row.created_at else None
    return data
