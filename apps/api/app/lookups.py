from typing import TypeVar

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db import Base

M = TypeVar("M", bound=Base)


def get_or_404(session: Session, model: type[M], id_: int) -> M:
    instance = session.get(model, id_)
    if instance is None:
        raise HTTPException(status_code=404, detail=f"{model.__name__} {id_} not found")
    return instance
