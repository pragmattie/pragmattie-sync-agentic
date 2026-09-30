from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.lookups import get_or_404
from app.models import Lead, Rep
from app.schemas import LeadCreate, LeadOut, LeadSource, LeadStatus, LeadUpdate, Page

router = APIRouter(prefix="/api/v1")

SORTS = {
    "company": Lead.company,
    "created_at": Lead.created_at,
    "last_name": Lead.last_name,
    "score": Lead.score,
    "status": Lead.status,
}


def _order_by(sort: str):
    column = SORTS.get(sort.removeprefix("-"))
    if column is None:
        raise HTTPException(status_code=400, detail=f"sort must be one of {sorted(SORTS)}")
    return (column.desc() if sort.startswith("-") else column.asc()), Lead.id.desc()


@router.get("/leads", response_model=Page[LeadOut])
def list_leads(
    q: str | None = None,
    status: Annotated[list[LeadStatus] | None, Query()] = None,
    source: LeadSource | None = None,
    owner_id: int | None = None,
    sort: str = "-created_at",
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> Page[LeadOut]:
    order_by = _order_by(sort)
    filters = []
    if q:
        needle = q.lower()
        filters.append(
            or_(
                *(
                    func.lower(column).contains(needle, autoescape=True)
                    for column in (Lead.first_name, Lead.last_name, Lead.company, Lead.email)
                )
            )
        )
    if status:
        filters.append(Lead.status.in_(status))
    if source is not None:
        filters.append(Lead.source == source)
    if owner_id is not None:
        filters.append(Lead.owner_id == owner_id)

    total = session.scalar(select(func.count()).select_from(Lead).where(*filters))
    leads = session.scalars(
        select(Lead)
        .where(*filters)
        .options(selectinload(Lead.owner))
        .order_by(*order_by)
        .limit(limit)
        .offset(offset)
    )
    return Page(items=[LeadOut.model_validate(lead) for lead in leads], total=total)


@router.post("/leads", response_model=LeadOut, status_code=201)
def create_lead(payload: LeadCreate, session: Session = Depends(get_session)) -> LeadOut:
    if payload.owner_id is not None:
        get_or_404(session, Rep, payload.owner_id)
    lead = Lead(**payload.model_dump())
    session.add(lead)
    session.commit()
    session.refresh(lead)
    return LeadOut.model_validate(lead)


@router.get("/leads/{lead_id}", response_model=LeadOut)
def get_lead(lead_id: int, session: Session = Depends(get_session)) -> LeadOut:
    return LeadOut.model_validate(get_or_404(session, Lead, lead_id))


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: int, payload: LeadUpdate, session: Session = Depends(get_session)
) -> LeadOut:
    lead = get_or_404(session, Lead, lead_id)
    if lead.status == "converted":
        raise HTTPException(status_code=409, detail="Converted leads cannot be edited")
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("owner_id") is not None:
        get_or_404(session, Rep, changes["owner_id"])
    for field, value in changes.items():
        setattr(lead, field, value)
    session.commit()
    session.refresh(lead)
    return LeadOut.model_validate(lead)
