from decimal import Decimal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.lookups import get_or_404
from app.models import Account, Contact, Rep
from app.schemas import (
    AccountCreate,
    AccountDetail,
    AccountOut,
    AccountUpdate,
    ContactOut,
    OwnerOut,
    Page,
)

router = APIRouter(prefix="/api/v1")

# Opportunities arrive with 1.3; until then every account has no open pipeline.
NO_PIPELINE = Decimal("0.00")


def _account_out(account: Account, contact_count: int) -> AccountOut:
    return AccountOut(
        id=account.id,
        name=account.name,
        industry=account.industry,
        employee_count=account.employee_count,
        annual_revenue=account.annual_revenue,
        region=account.region,
        website=account.website,
        owner_id=account.owner_id,
        owner=OwnerOut.model_validate(account.owner) if account.owner else None,
        created_at=account.created_at,
        open_pipeline=NO_PIPELINE,
        contact_count=contact_count,
    )


def _contact_count(session: Session, account_id: int) -> int:
    return session.scalar(select(func.count(Contact.id)).where(Contact.account_id == account_id))


@router.get("/accounts", response_model=Page[AccountOut])
def list_accounts(
    q: str | None = None,
    industry: str | None = None,
    owner_id: int | None = None,
    limit: int = Query(25, ge=1, le=200),
    offset: int = Query(0, ge=0),
    session: Session = Depends(get_session),
) -> Page[AccountOut]:
    filters = []
    if q:
        filters.append(func.lower(Account.name).contains(q.lower(), autoescape=True))
    if industry is not None:
        filters.append(Account.industry == industry)
    if owner_id is not None:
        filters.append(Account.owner_id == owner_id)

    total = session.scalar(select(func.count()).select_from(Account).where(*filters))

    counts = (
        select(Contact.account_id, func.count(Contact.id).label("contact_count"))
        .group_by(Contact.account_id)
        .subquery()
    )
    rows = session.execute(
        select(Account, func.coalesce(counts.c.contact_count, 0))
        .outerjoin(counts, counts.c.account_id == Account.id)
        .where(*filters)
        .options(selectinload(Account.owner))
        .order_by(Account.name, Account.id)
        .limit(limit)
        .offset(offset)
    ).all()
    return Page(items=[_account_out(account, count) for account, count in rows], total=total)


@router.post("/accounts", response_model=AccountOut, status_code=201)
def create_account(payload: AccountCreate, session: Session = Depends(get_session)) -> AccountOut:
    if payload.owner_id is not None:
        get_or_404(session, Rep, payload.owner_id)
    account = Account(**payload.model_dump())
    session.add(account)
    session.commit()
    session.refresh(account)
    return _account_out(account, 0)


@router.get("/accounts/{account_id}", response_model=AccountDetail)
def get_account(account_id: int, session: Session = Depends(get_session)) -> AccountDetail:
    account = get_or_404(session, Account, account_id)
    return AccountDetail(
        **_account_out(account, len(account.contacts)).model_dump(),
        contacts=[ContactOut.model_validate(contact) for contact in account.contacts],
        opportunities=[],
    )


@router.patch("/accounts/{account_id}", response_model=AccountOut)
def update_account(
    account_id: int, payload: AccountUpdate, session: Session = Depends(get_session)
) -> AccountOut:
    account = get_or_404(session, Account, account_id)
    changes = payload.model_dump(exclude_unset=True)
    if changes.get("owner_id") is not None:
        get_or_404(session, Rep, changes["owner_id"])
    for field, value in changes.items():
        setattr(account, field, value)
    session.commit()
    session.refresh(account)
    return _account_out(account, _contact_count(session, account.id))
