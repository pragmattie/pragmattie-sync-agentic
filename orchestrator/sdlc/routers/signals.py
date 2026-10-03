"""Read-only engineering signals for Delivery Insights, straight from ``sdlc.metrics``."""

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from sdlc import metrics
from sdlc.db import get_session

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


def get_now() -> datetime:
    """The current time without microseconds; tests override it to fix "now"."""
    return datetime.now().replace(microsecond=0)


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
