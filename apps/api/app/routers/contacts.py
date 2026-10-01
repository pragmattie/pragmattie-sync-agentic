from fastapi import APIRouter, Depends
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db import get_session
from app.lookups import get_or_404
from app.models import Account, Contact
from app.paging import DEFAULT_LIMIT, Limit, Offset
from app.schemas import ContactCreate, ContactOut, Page

router = APIRouter(prefix="/api/v1")


@router.get("/contacts", response_model=Page[ContactOut])
def list_contacts(
    account_id: int | None = None,
    q: str | None = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
    session: Session = Depends(get_session),
) -> Page[ContactOut]:
    filters = []
    if account_id is not None:
        filters.append(Contact.account_id == account_id)
    if q:
        needle = q.lower()
        filters.append(
            or_(
                *(
                    func.lower(column).contains(needle, autoescape=True)
                    for column in (Contact.first_name, Contact.last_name, Contact.email)
                )
            )
        )

    total = session.scalar(select(func.count()).select_from(Contact).where(*filters))
    contacts = session.scalars(
        select(Contact)
        .where(*filters)
        .order_by(Contact.last_name, Contact.first_name, Contact.id)
        .limit(limit)
        .offset(offset)
    )
    return Page(items=[ContactOut.model_validate(contact) for contact in contacts], total=total)


@router.post("/contacts", response_model=ContactOut, status_code=201)
def create_contact(payload: ContactCreate, session: Session = Depends(get_session)) -> Contact:
    get_or_404(session, Account, payload.account_id)
    contact = Contact(**payload.model_dump())
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact
