"""`/summary` figures come from the database (COUNT, SUM, GROUP BY), not from loaded rows."""

from datetime import date

from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.forecast import parse_quarter
from app.models import Lead, Opportunity
from app.seed import seed_database
from app.summary import build_summary, query_summary

TODAY = date(2026, 10, 1)


def test_query_summary_matches_the_row_by_row_reference_on_seed_data(sqlite_engine):
    seed_database(sqlite_engine, today=TODAY)
    _label, start, end = parse_quarter(None, TODAY)

    with Session(sqlite_engine) as session:
        from_sql = query_summary(session, start, end)
        leads = session.scalars(select(Lead)).all()
        opportunities = session.scalars(select(Opportunity)).all()
        from_rows = build_summary(leads, opportunities, start, end)

    assert from_sql == from_rows
    assert from_sql["open_deals"] > 0
    assert from_sql["won_this_quarter"] >= 0


def test_query_summary_on_an_empty_database(sqlite_engine):
    _label, start, end = parse_quarter(None, TODAY)

    with Session(sqlite_engine) as session:
        summary = query_summary(session, start, end)

    assert summary == build_summary([], [], start, end)


def test_summary_endpoint_only_runs_aggregate_queries(client, sqlite_engine):
    seed_database(sqlite_engine, today=TODAY)
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(sqlite_engine, "before_cursor_execute", record)
    try:
        response = client.get("/api/v1/summary")
    finally:
        event.remove(sqlite_engine, "before_cursor_execute", record)

    assert response.status_code == 200
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    assert selects
    for statement in selects:
        assert "count(" in statement.lower() or "sum(" in statement.lower(), statement
