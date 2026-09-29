from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError


def make_engine(database_url: str) -> Engine:
    return create_engine(database_url, pool_pre_ping=True)


def database_status(engine: Engine) -> str:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return "unavailable"
    return "ok"
