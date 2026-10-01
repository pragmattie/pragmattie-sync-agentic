from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.lookups import check_owner, get_or_404
from app.models import Account, Opportunity
from app.paging import DEFAULT_OPPORTUNITY_LIMIT, Offset, OpportunityLimit
from app.schemas import OpportunityCreate, OpportunityOut, OpportunityUpdate, Page
from app.stages import STAGE_PROBABILITY, Stage

router = APIRouter(prefix="/api/v1", tags=["opportunities"])


@router.get(
    "/opportunities", response_model=Page[OpportunityOut], summary="List and filter opportunities"
)
def list_opportunities(
    stage: Annotated[
        list[Stage] | None, Query(description="Only deals in these stages (repeatable).")
    ] = None,
    owner_id: Annotated[int | None, Query(description="Only deals this rep owns.")] = None,
    account_id: Annotated[int | None, Query(description="Only this account's deals.")] = None,
    close_from: Annotated[
        date | None, Query(description="Only deals closing on or after this date.")
    ] = None,
    close_to: Annotated[
        date | None, Query(description="Only deals closing on or before this date.")
    ] = None,
    q: Annotated[str | None, Query(description="Text to find in the deal name.")] = None,
    limit: OpportunityLimit = DEFAULT_OPPORTUNITY_LIMIT,
    offset: Offset = 0,
    session: Session = Depends(get_session),
) -> Page[OpportunityOut]:
    filters = []
    if stage:
        filters.append(Opportunity.stage.in_(stage))
    if owner_id is not None:
        filters.append(Opportunity.owner_id == owner_id)
    if account_id is not None:
        filters.append(Opportunity.account_id == account_id)
    if close_from is not None:
        filters.append(Opportunity.close_date >= close_from)
    if close_to is not None:
        filters.append(Opportunity.close_date <= close_to)
    if q:
        filters.append(func.lower(Opportunity.name).contains(q.lower(), autoescape=True))

    total = session.scalar(select(func.count()).select_from(Opportunity).where(*filters))
    opportunities = session.scalars(
        select(Opportunity)
        .where(*filters)
        .options(selectinload(Opportunity.account), selectinload(Opportunity.owner))
        .order_by(Opportunity.close_date, Opportunity.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(
        items=[OpportunityOut.model_validate(opportunity) for opportunity in opportunities],
        total=total,
    )


@router.post(
    "/opportunities",
    response_model=OpportunityOut,
    status_code=201,
    summary="Create an opportunity",
)
def create_opportunity(
    payload: OpportunityCreate, session: Session = Depends(get_session)
) -> OpportunityOut:
    get_or_404(session, Account, payload.account_id)
    check_owner(session, payload.owner_id)
    values = payload.model_dump()
    if values["probability"] is None:
        values["probability"] = STAGE_PROBABILITY[payload.stage]
    opportunity = Opportunity(**values)
    session.add(opportunity)
    session.commit()
    session.refresh(opportunity)
    return OpportunityOut.model_validate(opportunity)


@router.get(
    "/opportunities/{opportunity_id}", response_model=OpportunityOut, summary="Get an opportunity"
)
def get_opportunity(
    opportunity_id: Annotated[int, Path(description="The opportunity's id.")],
    session: Session = Depends(get_session),
) -> OpportunityOut:
    return OpportunityOut.model_validate(get_or_404(session, Opportunity, opportunity_id))


@router.patch(
    "/opportunities/{opportunity_id}",
    response_model=OpportunityOut,
    summary="Change some of an opportunity's fields, such as its stage",
)
def update_opportunity(
    opportunity_id: Annotated[int, Path(description="The opportunity's id.")],
    payload: OpportunityUpdate,
    session: Session = Depends(get_session),
) -> OpportunityOut:
    opportunity = get_or_404(session, Opportunity, opportunity_id)
    changes = payload.model_dump(exclude_unset=True)
    if "account_id" in changes:
        get_or_404(session, Account, changes["account_id"])
    check_owner(session, changes.get("owner_id"))
    # A new stage brings its default probability, unless the request sets one itself.
    new_stage = changes.get("stage", opportunity.stage)
    if new_stage != opportunity.stage and "probability" not in changes:
        changes["probability"] = STAGE_PROBABILITY[new_stage]
    for field, value in changes.items():
        setattr(opportunity, field, value)
    session.commit()
    session.refresh(opportunity)
    return OpportunityOut.model_validate(opportunity)
