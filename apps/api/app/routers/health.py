from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from app.db import database_is_reachable, get_engine

router = APIRouter(prefix="/api/v1", tags=["health"])


@router.get("/health", summary="Check the API and its database connection")
def health(engine: Engine = Depends(get_engine)) -> dict[str, str]:
    return {
        "status": "ok",
        "service": "crm-api",
        "database": "ok" if database_is_reachable(engine) else "unavailable",
    }
