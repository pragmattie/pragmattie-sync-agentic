from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.forecast import parse_quarter
from app.models import Lead, Opportunity
from app.schemas import SummaryOut
from app.summary import build_summary

router = APIRouter(prefix="/api/v1")


@router.get("/summary", response_model=SummaryOut)
def get_summary(session: Session = Depends(get_session)) -> SummaryOut:
    quarter_label, start, end = parse_quarter(None, datetime.now(UTC).date())

    leads = session.scalars(select(Lead)).all()
    opportunities = session.scalars(select(Opportunity)).all()

    summary = build_summary(leads, opportunities, start, end)
    return SummaryOut(quarter=quarter_label, **summary)
