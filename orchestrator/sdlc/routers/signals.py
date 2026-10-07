"""Read-only engineering signals for Delivery Insights, from ``sdlc.metrics`` and ``sdlc.audit``."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from sdlc import audit, metrics
from sdlc.clock import utcnow
from sdlc.db import get_session
from sdlc.tables import AgentDecision

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


def get_now() -> datetime:
    """The current UTC time without microseconds; tests override it to fix "now"."""
    return utcnow()


@router.get("/summary")
def summary(
    days: int = Query(30, ge=7, le=180),
    db: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> dict:
    return metrics.dora_summary(db, now, days)


@router.get("/sprints")
def sprints(db: Session = Depends(get_session), now: datetime = Depends(get_now)) -> list[dict]:
    return metrics.sprint_velocity(db, now.date())


@router.get("/cycle-time")
def cycle_time(
    bucket: Literal["sprint", "week"] = "sprint",
    weeks: int = Query(26, ge=1, le=104),
    db: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> list[dict]:
    if bucket == "week":
        return metrics.pr_cycle_time(db, weeks, now)
    return metrics.pr_cycle_time_by_sprint(db, now)


@router.get("/ci")
def ci(
    weeks: int = Query(26, ge=1, le=104),
    db: Session = Depends(get_session),
    now: datetime = Depends(get_now),
) -> dict:
    return metrics.ci_health(db, weeks, now)


@router.get("/modules")
def modules(db: Session = Depends(get_session)) -> list[dict]:
    return metrics.quality_by_module(db)


@router.get("/sources")
def sources(db: Session = Depends(get_session)) -> dict[str, dict[str, int]]:
    return metrics.sources(db)


@router.get("/decisions")
def decisions(
    agent: str | None = None,
    subject_type: Literal["pr", "issue", "sprint", "epic", "release"] | None = None,
    subject_source: Literal["synthetic", "github"] | None = None,
    status: str | None = None,
    tier: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_session),
) -> dict:
    """The audit trail newest first, one page of it, with totals over every matching row."""
    filters = {
        "agent": agent,
        "subject_type": subject_type,
        "subject_source": subject_source,
        "status": status,
        "tier": tier,
    }
    rows = audit.list_decisions(db, limit=limit, offset=offset, **filters)
    return {
        "total": audit.count_decisions(db, **filters),
        "limit": limit,
        "offset": offset,
        "decisions": [audit.serialize_decision(row) for row in rows],
        "totals": audit.totals(db, **filters),
    }


@router.get("/decisions/agents")
def decision_agents(db: Session = Depends(get_session)) -> list[dict]:
    return audit.agents(db)


@router.get("/decisions/{decision_id}")
def decision(decision_id: int, db: Session = Depends(get_session)) -> dict:
    row = db.get(AgentDecision, decision_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"No decision {decision_id}.")
    return audit.serialize_decision(row)
