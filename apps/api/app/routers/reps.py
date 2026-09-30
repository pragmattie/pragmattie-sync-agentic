from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Rep
from app.schemas import RepOut

router = APIRouter(prefix="/api/v1")


@router.get("/reps", response_model=list[RepOut])
def list_reps(session: Session = Depends(get_session)) -> list[Rep]:
    return list(session.scalars(select(Rep).order_by(Rep.name, Rep.id)))
