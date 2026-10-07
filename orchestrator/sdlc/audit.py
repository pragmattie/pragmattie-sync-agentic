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


def totals(
    db: Session,
    *,
    agent: str | None = None,
    subject_type: str | None = None,
    subject_source: str | None = None,
    status: str | None = None,
    tier: str | None = None,
) -> dict[str, Any]:
    """Runs, tokens and cost over every decision matching the filters, trial rows included.

    Tokens are summed in SQL. Cost is summed here from each row's ``output["cost_usd"]``, since
    JSON paths differ between MySQL and SQLite; a row without one adds nothing and is counted.
    """
    filters = {
        "agent": agent,
        "subject_type": subject_type,
        "subject_source": subject_source,
        "status": status,
        "tier": tier,
    }
    runs, input_tokens, output_tokens = db.execute(
        _filtered(
            select(
                func.count(),
                func.coalesce(func.sum(AgentDecision.input_tokens), 0),
                func.coalesce(func.sum(AgentDecision.output_tokens), 0),
            ).select_from(AgentDecision),
            **filters,
        )
    ).one()
    cost, without_cost = 0.0, 0
    for output in db.scalars(_filtered(select(AgentDecision.output), **filters)):
        value = output.get("cost_usd") if isinstance(output, dict) else None
        if isinstance(value, int | float) and not isinstance(value, bool):
            cost += value
        else:
            without_cost += 1
    return {
        "runs": runs,
        "input_tokens": int(input_tokens),
        "output_tokens": int(output_tokens),
        "cost_usd": round(cost, 4),
        "runs_without_cost": without_cost,
    }


def agents(db: Session) -> list[dict[str, Any]]:
    """Every agent with decisions and how many it has, most runs first."""
    runs = func.count().label("runs")
    query = (
        select(AgentDecision.agent, runs)
        .group_by(AgentDecision.agent)
        .order_by(runs.desc(), AgentDecision.agent)
    )
    return [{"agent": agent, "runs": count} for agent, count in db.execute(query)]
