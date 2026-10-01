from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.forecast import build_forecast, parse_quarter
from app.models import Opportunity, Rep
from app.schemas import ForecastOut

router = APIRouter(prefix="/api/v1", tags=["forecast"])


@router.get("/forecast", response_model=ForecastOut, summary="Get the sales forecast for a quarter")
def get_forecast(
    quarter: Annotated[
        str | None,
        Query(description='Quarter as "YYYY-Qn", e.g. "2026-Q3"; defaults to the current quarter.'),
    ] = None,
    session: Session = Depends(get_session),
) -> ForecastOut:
    try:
        quarter_label, start, end = parse_quarter(quarter, datetime.now(UTC).date())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    reps = session.scalars(select(Rep).order_by(Rep.name, Rep.id)).all()
    opportunities = session.scalars(
        select(Opportunity).where(Opportunity.close_date >= start, Opportunity.close_date <= end)
    ).all()

    forecast = build_forecast(reps, opportunities, start, end)
    return ForecastOut(quarter=quarter_label, start=start, end=end, **forecast)
