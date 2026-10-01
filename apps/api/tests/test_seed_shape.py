"""The seed data's shape, rule by rule from its spec (1.9)."""

from collections import Counter
from datetime import date, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.forecast import parse_quarter
from app.models import Account, Contact, Lead, Opportunity, Rep
from app.seed import seed_database
from app.stages import OPEN_STAGES, STAGE_PROBABILITY

TODAY = date(2026, 10, 1)
MID_QUARTER = date(2026, 11, 14)
LARGEST_DEAL = Decimal("120000")


@pytest.fixture
def seeded(sqlite_engine):
    """Seed for a given day and return every row, read in id order."""

    def seed(today=TODAY):
        seed_database(sqlite_engine, today=today)
        with Session(sqlite_engine) as session:
            rows = {
                name: session.scalars(select(model).order_by(model.id)).all()
                for name, model in [
                    ("reps", Rep),
                    ("accounts", Account),
                    ("contacts", Contact),
                    ("opportunities", Opportunity),
                    ("leads", Lead),
                ]
            }
            session.expunge_all()
        return rows

    return seed


def _quarter(label):
    _label, start, end = parse_quarter(label, TODAY)
    return start, end


def _won(deals, rep, start, end):
    return sum(
        (
            d.amount
            for d in deals
            if d.owner_id == rep.id and d.stage == "closed_won" and start <= d.close_date <= end
        ),
        Decimal(0),
    )


def test_six_reps_by_region_with_quotas_of_320k_to_450k(seeded):
    reps = seeded()["reps"]

    assert sorted(r.region for r in reps) == sorted(
        ["East", "West", "Central", "EMEA", "EMEA", "APAC"]
    )
    for rep in reps:
        assert Decimal("320000") <= rep.quarterly_quota <= Decimal("450000")


def test_accounts_have_an_owner_a_size_revenue_and_age_in_range(seeded):
    accounts = seeded()["accounts"]

    for account in accounts:
        assert account.owner_id is not None
        assert 40 <= account.employee_count <= 5000
        # Revenue is in proportion to size: a steady band of revenue per employee.
        per_employee = account.annual_revenue / account.employee_count
        assert Decimal("100000") <= per_employee <= Decimal("300000"), account.name
        age = TODAY - account.created_at.date()
        assert timedelta(days=118) <= age <= timedelta(days=731), account.name


def test_account_names_are_word_pairs_and_industry_follows_the_name(seeded):
    accounts = seeded()["accounts"]

    industry_by_last_word = {}
    for account in accounts:
        words = account.name.split()
        assert len(words) == 3, account.name
        industry_by_last_word.setdefault(words[-1], set()).add(account.industry)
    assert all(len(industries) == 1 for industries in industry_by_last_word.values())
    assert len({a.name for a in accounts}) == len(accounts)


def test_each_account_has_one_to_four_titled_contacts_with_555_numbers(seeded):
    data = seeded()

    per_account = Counter(c.account_id for c in data["contacts"])
    assert set(per_account) == {a.id for a in data["accounts"]}
    assert all(1 <= n <= 4 for n in per_account.values())
    for contact in data["contacts"]:
        assert contact.phone.startswith("+1-555-")
        assert contact.title


def test_deals_are_round_amounts_with_their_stage_probability_and_account_name(seeded):
    data = seeded()
    names = {a.id: a.name for a in data["accounts"]}

    for deal in data["opportunities"]:
        assert Decimal("12000") <= deal.amount <= LARGEST_DEAL
        assert deal.amount % 1000 == 0
        assert deal.probability == STAGE_PROBABILITY[deal.stage]
        assert deal.name.startswith(f"{names[deal.account_id]} - ")
        assert deal.owner_id is not None


@pytest.mark.parametrize("label", ["2026-Q2", "2026-Q3"])
def test_each_of_the_last_two_quarters_has_some_losses_and_no_open_deals(seeded, label):
    data = seeded()
    start, end = _quarter(label)

    for rep in data["reps"]:
        lost = [
            d
            for d in data["opportunities"]
            if d.owner_id == rep.id and d.stage == "closed_lost" and start <= d.close_date <= end
        ]
        assert lost
    assert not [
        d for d in data["opportunities"] if d.stage in OPEN_STAGES and start <= d.close_date <= end
    ]


def test_last_two_quarters_won_is_strictly_80_to_115_percent_of_quota(seeded):
    data = seeded()

    for label in ("2026-Q2", "2026-Q3"):
        start, end = _quarter(label)
        for rep in data["reps"]:
            won = _won(data["opportunities"], rep, start, end)
            assert Decimal("0.80") <= won / rep.quarterly_quota <= Decimal("1.15")


def test_current_quarter_wins_so_far_track_the_time_elapsed(seeded):
    data = seeded(MID_QUARTER)
    _label, start, end = parse_quarter(None, MID_QUARTER)
    elapsed = Decimal((MID_QUARTER - start).days + 1) / Decimal((end - start).days + 1)

    for rep in data["reps"]:
        rep_won = [
            d
            for d in data["opportunities"]
            if d.owner_id == rep.id and d.stage == "closed_won" and start <= d.close_date <= end
        ]
        assert all(d.close_date <= MID_QUARTER for d in rep_won)
        won = sum((d.amount for d in rep_won), Decimal(0))
        pro_rata = rep.quarterly_quota * elapsed
        assert pro_rata * Decimal("0.55") <= won <= pro_rata + LARGEST_DEAL


def test_open_deals_this_quarter_close_from_tomorrow_and_make_quota_possible(seeded):
    data = seeded(MID_QUARTER)
    _label, start, end = parse_quarter(None, MID_QUARTER)

    open_now = [d for d in data["opportunities"] if d.stage in OPEN_STAGES and d.close_date <= end]
    assert open_now
    assert all(MID_QUARTER < d.close_date for d in open_now)

    quota = sum((r.quarterly_quota for r in data["reps"]), Decimal(0))
    won = sum((_won(data["opportunities"], r, start, end) for r in data["reps"]), Decimal(0))
    pipeline = sum((d.amount for d in open_now), Decimal(0))
    assert won < quota < won + pipeline


def test_open_deal_stages_lean_later_nearer_their_close_date(seeded):
    data = seeded()
    _label, _start, end = parse_quarter(None, TODAY)
    order = {stage: i for i, stage in enumerate(OPEN_STAGES)}

    open_now = [d for d in data["opportunities"] if d.stage in OPEN_STAGES and d.close_date <= end]
    near = [order[d.stage] for d in open_now if (d.close_date - TODAY).days <= 14]
    far = [order[d.stage] for d in open_now if (d.close_date - TODAY).days > 45]
    assert near and far
    assert sum(near) / len(near) > sum(far) / len(far)


def test_next_quarter_has_about_twice_quota_of_mostly_early_open_deals(seeded):
    data = seeded()
    start, end = _quarter("2027-Q1")

    for rep in data["reps"]:
        deals = [d for d in data["opportunities"] if d.owner_id == rep.id]
        next_quarter = [d for d in deals if start <= d.close_date <= end]
        assert all(d.stage in OPEN_STAGES for d in next_quarter)
        total = sum((d.amount for d in next_quarter), Decimal(0))
        assert 2 * rep.quarterly_quota <= total <= 2 * rep.quarterly_quota + LARGEST_DEAL
        early = [d for d in next_quarter if d.stage in ("prospecting", "qualification")]
        assert len(early) > len(next_quarter) / 2
    assert all(d.close_date <= end for d in data["opportunities"])


def test_leads_are_from_the_last_90_days_across_five_sources_web_first(seeded):
    leads = seeded()["leads"]

    for lead in leads:
        assert timedelta(0) <= TODAY - lead.created_at.date() <= timedelta(days=90)
    sources = Counter(lead.source for lead in leads)
    assert set(sources) == {"web", "referral", "event", "outbound", "partner"}
    assert sources.most_common(1)[0][0] == "web"


def test_newer_leads_are_mostly_new_and_older_ones_spread_out(seeded):
    leads = seeded()["leads"]

    newer = [lead.status for lead in leads if (TODAY - lead.created_at.date()).days < 14]
    older = Counter(lead.status for lead in leads if (TODAY - lead.created_at.date()).days >= 45)
    assert newer
    assert newer.count("new") > len(newer) / 2
    assert {"working", "qualified", "disqualified"} <= set(older)
    assert "converted" not in {lead.status for lead in leads}


def test_lead_scores_fit_their_status(seeded):
    leads = seeded()["leads"]

    def mean(status):
        scores = [lead.score for lead in leads if lead.status == status]
        return sum(scores) / len(scores)

    assert mean("qualified") > mean("working") > mean("new")
    assert mean("disqualified") < mean("qualified")
    assert all(0 <= lead.score <= 100 for lead in leads)
