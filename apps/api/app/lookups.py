from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db import Base
from app.models import Rep

M = TypeVar("M", bound=Base)


def get_or_404(session: Session, model: type[M], id_: int) -> M:
    instance = session.get(model, id_)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {id_} not found")
    return instance


def check_owner(session: Session, owner_id: int | None) -> None:
    """404 unless `owner_id` is empty or names an existing rep."""
    if owner_id is not None:
        get_or_404(session, Rep, owner_id)
