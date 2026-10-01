from datetime import UTC, datetime

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db import get_session
from app.forecast import parse_quarter
from app.schemas import SummaryOut
from app.summary import query_summary

router = APIRouter(prefix="/api/v1")


@router.get("/summary", response_model=SummaryOut)
def get_summary(session: Session = Depends(get_session)) -> SummaryOut:
    quarter_label, start, end = parse_quarter(None, datetime.now(UTC).date())
    return SummaryOut(quarter=quarter_label, **query_summary(session, start, end))
