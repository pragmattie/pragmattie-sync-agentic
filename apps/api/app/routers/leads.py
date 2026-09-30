from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.lookups import get_or_404
from app.models import Account, Contact, Lead, Opportunity, Rep
from app.schemas import (
    LeadConvertRequest,
    LeadConvertResponse,
    LeadCreate,
    LeadOut,
    LeadSource,
    LeadStatus,
    LeadUpdate,
    Page,
)
from app.stages import STAGE_PROBABILITY

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


@router.post("/leads/{lead_id}/convert", response_model=LeadConvertResponse)
def convert_lead(
    lead_id: int, payload: LeadConvertRequest, session: Session = Depends(get_session)
) -> LeadConvertResponse:
    lead = get_or_404(session, Lead, lead_id)
    if lead.status == "converted":
        raise HTTPException(status_code=409, detail="Lead is already converted")
    if lead.status == "disqualified":
        raise HTTPException(status_code=409, detail="Disqualified leads cannot be converted")

    account = Account(
        name=lead.company,
        industry=payload.industry,
        employee_count=payload.employee_count,
        annual_revenue=payload.annual_revenue,
        region=payload.region,
        owner_id=lead.owner_id,
    )
    contact = Contact(
        first_name=lead.first_name,
        last_name=lead.last_name,
        email=lead.email,
        title=lead.title,
        account=account,
    )
    opportunity = None
    if payload.opportunity_amount is not None:
        opportunity = Opportunity(
            name=payload.opportunity_name or f"{lead.company} - New business",
            amount=payload.opportunity_amount,
            stage="qualification",
            probability=STAGE_PROBABILITY["qualification"],
            close_date=payload.opportunity_close_date or date.today() + timedelta(days=60),
            owner_id=lead.owner_id,
            account=account,
        )

    session.add(account)
    session.add(contact)
    if opportunity is not None:
        session.add(opportunity)
    session.flush()

    lead.status = "converted"
    lead.converted_account_id = account.id

    session.commit()
    session.refresh(lead)

    return LeadConvertResponse(
        lead=LeadOut.model_validate(lead),
        account_id=account.id,
        contact_id=contact.id,
        opportunity_id=opportunity.id if opportunity else None,
    )
