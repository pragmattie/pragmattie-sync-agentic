from fastapi import APIRouter, Depends
from sqlalchemy.engine import Engine

from sdlc.db import database_is_reachable, get_engine

router = APIRouter(prefix="/api/v1")


@router.get("/health")
def health(engine: Engine = Depends(get_engine)) -> dict[str, str]:
    return {
        "status": "ok",
        "service": "orchestrator",
        "database": "ok" if database_is_reachable(engine) else "unavailable",
    }
