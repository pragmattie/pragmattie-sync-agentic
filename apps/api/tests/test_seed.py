from datetime import date

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db import Base
from app.forecast import build_forecast, parse_quarter
from app.models import Account, Contact, Lead, Opportunity, Rep
from app.seed import SeedError, seed_database

TODAY = date(2026, 10, 1)


def _counts(engine):
    with Session(engine) as session:
        return {
            "reps": len(session.scalars(select(Rep)).all()),
            "accounts": len(session.scalars(select(Account)).all()),
            "contacts": len(session.scalars(select(Contact)).all()),
            "opportunities": len(session.scalars(select(Opportunity)).all()),
            "leads": len(session.scalars(select(Lead)).all()),
        }


def _snapshot(engine):
    with Session(engine) as session:
        reps = [
            (r.id, r.name, r.email, r.region, str(r.quarterly_quota))
            for r in session.scalars(select(Rep).order_by(Rep.id))
        ]
        accounts = [
            (
                a.id,
                a.name,
                a.industry,
                a.employee_count,
                str(a.annual_revenue),
                a.region,
                a.website,
                a.owner_id,
                a.created_at,
            )
            for a in session.scalars(select(Account).order_by(Account.id))
        ]
        contacts = [
            (c.id, c.account_id, c.first_name, c.last_name, c.email, c.title, c.phone)
            for c in session.scalars(select(Contact).order_by(Contact.id))
        ]
        opportunities = [
            (
                o.id,
                o.account_id,
                o.name,
                str(o.amount),
                o.stage,
                o.probability,
                o.close_date,
                o.owner_id,
            )
            for o in session.scalars(select(Opportunity).order_by(Opportunity.id))
        ]
        leads = [
            (
                lead.id,
                lead.first_name,
                lead.last_name,
                lead.email,
                lead.company,
                lead.title,
                lead.source,
                lead.status,
                lead.score,
                lead.owner_id,
                lead.created_at,
            )
            for lead in session.scalars(select(Lead).order_by(Lead.id))
        ]
        return reps, accounts, contacts, opportunities, leads


def test_seed_creates_the_expected_counts(sqlite_engine):
    counts = seed_database(sqlite_engine, today=TODAY)

    assert counts.reps == 6
    assert counts.accounts == 60
    assert 60 <= counts.contacts <= 240
    assert counts.leads == 220
    assert counts.opportunities > 0
    assert _counts(sqlite_engine) == {
        "reps": 6,
        "accounts": 60,
        "contacts": counts.contacts,
        "opportunities": counts.opportunities,
        "leads": 220,
    }


def test_seed_only_uses_example_domains(sqlite_engine):
    seed_database(sqlite_engine, today=TODAY)

    with Session(sqlite_engine) as session:
        for rep in session.scalars(select(Rep)):
            assert rep.email.endswith("@pragmattie-sync.example")
        for account in session.scalars(select(Account)):
            assert account.website.endswith(".example")
        for contact in session.scalars(select(Contact)):
            assert contact.email.endswith(".example")
        for lead in session.scalars(select(Lead)):
            assert lead.email.endswith(".example")


def test_seed_refuses_when_reps_already_exist(sqlite_engine):
    seed_database(sqlite_engine, today=TODAY)

    with pytest.raises(SeedError):
        seed_database(sqlite_engine, today=TODAY)


def test_seed_if_empty_skips_when_data_exists(sqlite_engine):
    first = seed_database(sqlite_engine, today=TODAY)

    result = seed_database(sqlite_engine, mode="if_empty", today=TODAY)

    assert result is None
    assert _counts(sqlite_engine) == {
        "reps": first.reps,
        "accounts": first.accounts,
        "contacts": first.contacts,
        "opportunities": first.opportunities,
        "leads": first.leads,
    }


def test_seed_if_empty_seeds_when_database_is_empty(sqlite_engine):
    result = seed_database(sqlite_engine, mode="if_empty", today=TODAY)

    assert result is not None
    assert result.reps == 6


def test_seed_reset_replaces_data_and_restarts_ids(sqlite_engine):
    seed_database(sqlite_engine, today=TODAY)

    seed_database(sqlite_engine, mode="reset", today=TODAY)

    with Session(sqlite_engine) as session:
        rep_ids = [r.id for r in session.scalars(select(Rep).order_by(Rep.id))]
        account_ids = [a.id for a in session.scalars(select(Account).order_by(Account.id))]
    assert rep_ids == list(range(1, 7))
    assert account_ids == list(range(1, 61))


def test_seed_reset_on_an_empty_database_seeds_fresh(sqlite_engine):
    counts = seed_database(sqlite_engine, mode="reset", today=TODAY)

    assert counts.reps == 6


def test_seed_is_repeatable(sqlite_engine, tmp_path):
    seed_database(sqlite_engine, today=TODAY)
    first = _snapshot(sqlite_engine)

    other_engine = create_engine(f"sqlite:///{tmp_path / 'other.db'}")
    Base.metadata.create_all(other_engine)
    seed_database(other_engine, today=TODAY)
    second = _snapshot(other_engine)

    assert first == second


def test_seed_forecast_has_won_commit_and_pipeline_for_the_current_quarter(sqlite_engine):
    seed_database(sqlite_engine, today=TODAY)

    with Session(sqlite_engine) as session:
        reps = session.scalars(select(Rep)).all()
        _label, start, end = parse_quarter(None, TODAY)
        opportunities = session.scalars(
            select(Opportunity).where(
                Opportunity.close_date >= start, Opportunity.close_date <= end
            )
        ).all()
        forecast = build_forecast(reps, opportunities, start, end)

    assert forecast["won"] > 0
    assert forecast["commit"] > 0
    assert forecast["pipeline"] > 0
