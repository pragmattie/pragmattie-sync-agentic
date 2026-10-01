"""Deterministic demo data for the CRM.

Run from `apps/api` with `python -m app.seed` (add `--if-empty` or `--reset`; see
module docstring in issue 1.9 for the full spec). Every name, email and website is
invented and lives on the reserved `.example` domain. Generation uses a fixed random
seed and dates relative to `today`, so the same day always produces the same data.
"""

import argparse
import random
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import delete, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from app.db import get_engine
from app.models import Account, Contact, Lead, Opportunity, Rep
from app.stages import OPEN_STAGES, STAGE_PROBABILITY

_SEED = 20260131

REP_REGIONS = ["East", "West", "Central", "EMEA", "EMEA", "APAC"]

FIRST_NAMES = [
    "Avery", "Jordan", "Riley", "Casey", "Morgan", "Taylor", "Jamie", "Quinn",
    "Reese", "Harper", "Rowan", "Elliot", "Sawyer", "Dakota", "Emerson", "Finley",
    "Hayden", "Kai", "Marlowe", "Nova", "Parker", "Sage", "Wren", "Zion",
    "Priya", "Noah", "Mei", "Lucas", "Ines", "Omar",
]
LAST_NAMES = [
    "Park", "Cole", "Rivera", "Bennett", "Howard", "Fletcher", "Okafor", "Nakamura",
    "Dubois", "Alvarez", "Larsen", "Whitfield", "Mercer", "Donnelly", "Ibarra", "Sato",
    "Lindqvist", "Carrow", "Mbeki", "Petrova", "Hassan", "Kowalski", "Delgado", "Osei",
    "Franco", "Winters", "Castillo", "Varga", "Singh", "Novak",
]

ADJECTIVES = [
    "Cobalt", "Crimson", "Azure", "Granite", "Silver", "Golden", "Northern", "Summit",
    "Harbor", "Cedar", "Maple", "Amber", "Ironwood", "Vertex", "Lunar", "Solar",
    "Quartz", "Meridian", "Crescent", "Falcon",
]
NOUNS = [
    "Ridge", "Harbor", "Peak", "River", "Field", "Grove", "Bridge", "Hollow",
    "Bay", "Point", "Vale", "Crossing", "Orchard", "Mill", "Quarry", "Summit",
]
SUFFIX_INDUSTRY = {
    "Analytics": "Software",
    "Technologies": "Technology",
    "Systems": "Technology",
    "Software": "Software",
    "Logistics": "Logistics",
    "Freight": "Logistics",
    "Manufacturing": "Manufacturing",
    "Robotics": "Manufacturing",
    "Industries": "Manufacturing",
    "Health": "Healthcare",
    "Wellness": "Healthcare",
    "Capital": "Financial Services",
    "Financial": "Financial Services",
    "Partners": "Professional Services",
    "Consulting": "Professional Services",
    "Media": "Media",
    "Studios": "Media",
    "Foods": "Consumer Goods",
    "Goods": "Consumer Goods",
    "Energy": "Energy",
}

CONTACT_TITLES = [
    "VP of Sales", "Sales Manager", "Account Executive", "Director of Sales Operations",
    "Revenue Operations Manager", "Customer Success Manager", "VP of Operations",
    "Director of Procurement", "Operations Manager", "Business Development Manager",
]
LEAD_TITLES = [
    *CONTACT_TITLES, "CEO", "CTO", "VP of Marketing", "IT Director", "Procurement Lead",
]

LEAD_SOURCES = ["web", "referral", "event", "outbound", "partner"]
LEAD_SOURCE_WEIGHTS = [40, 20, 15, 15, 10]

ROUND_AMOUNTS = [
    12000, 15000, 18000, 20000, 22000, 25000, 28000, 32000, 35000, 40000, 45000,
    50000, 55000, 60000, 65000, 70000, 75000, 80000, 90000, 100000, 110000, 120000,
]
DEAL_SUFFIXES = ["Expansion", "Renewal", "New Business", "Platform Upgrade", "Upsell"]

RESET_TABLES = ("leads", "opportunities", "contacts", "accounts", "reps")


class SeedError(Exception):
    """Raised when seeding is refused, e.g. the database already has data."""


@dataclass
class SeedCounts:
    reps: int
    accounts: int
    contacts: int
    opportunities: int
    leads: int

    def __str__(self) -> str:
        return (
            f"Seeded {self.reps} reps, {self.accounts} accounts, {self.contacts} contacts, "
            f"{self.opportunities} opportunities and {self.leads} leads."
        )


def seed_database(
    engine: Engine, *, mode: str = "default", today: date | None = None
) -> SeedCounts | None:
    """Seed the CRM. `mode` is "default", "if_empty" or "reset". Returns `None` if
    "if_empty" skipped because the database already has reps."""
    today = today or date.today()
    with Session(engine) as session:
        has_reps = session.scalar(select(Rep.id).limit(1)) is not None

        if mode == "reset":
            _delete_all(session)
            _restart_auto_increment(engine)
        elif has_reps:
            if mode == "if_empty":
                return None
            raise SeedError(
                "Database already has reps; refusing to seed. "
                "Use --reset to replace it or --if-empty to skip."
            )

        counts = _generate(session, today)
        session.commit()
        return counts


def _delete_all(session: Session) -> None:
    session.execute(delete(Lead))
    session.execute(delete(Opportunity))
    session.execute(delete(Contact))
    session.execute(delete(Account))
    session.execute(delete(Rep))
    session.commit()


def _restart_auto_increment(engine: Engine) -> None:
    if engine.dialect.name != "mysql":
        return
    with engine.begin() as connection:
        for table in RESET_TABLES:
            connection.execute(text(f"ALTER TABLE {table} AUTO_INCREMENT = 1"))


def _generate(session: Session, today: date) -> SeedCounts:
    rng = random.Random(_SEED)

    reps = _make_reps(rng)
    session.add_all(reps)
    session.flush()

    accounts = _make_accounts(rng, reps, today)
    session.add_all(accounts)
    session.flush()

    contacts = _make_contacts(rng, accounts)
    session.add_all(contacts)

    opportunities = _make_opportunities(rng, reps, accounts, today)
    session.add_all(opportunities)

    leads = _make_leads(rng, reps, today)
    session.add_all(leads)

    return SeedCounts(
        reps=len(reps),
        accounts=len(accounts),
        contacts=len(contacts),
        opportunities=len(opportunities),
        leads=len(leads),
    )


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _broad_region(rep_region: str) -> str:
    return "North America" if rep_region in ("East", "West", "Central") else rep_region


def _pct(rng, lo: float, hi: float) -> Decimal:
    return Decimal(str(round(rng.uniform(lo, hi), 4)))


def _make_reps(rng) -> list[Rep]:
    reps = []
    seen_emails: set[str] = set()
    for region in REP_REGIONS:
        while True:
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            email = f"{first.lower()}.{last.lower()}@pragmattie-sync.example"
            if email not in seen_emails:
                seen_emails.add(email)
                break
        quota = Decimal(rng.randint(320, 450) * 1000)
        reps.append(Rep(name=f"{first} {last}", email=email, region=region, quarterly_quota=quota))
    return reps


def _make_accounts(rng, reps: list[Rep], today: date) -> list[Account]:
    accounts = []
    used_names: set[str] = set()
    for _ in range(60):
        while True:
            adjective, noun = rng.choice(ADJECTIVES), rng.choice(NOUNS)
            suffix = rng.choice(list(SUFFIX_INDUSTRY))
            name = f"{adjective} {noun} {suffix}"
            if name not in used_names:
                used_names.add(name)
                break

        employee_count = round(rng.triangular(40, 5000, 300))
        revenue_per_employee = rng.uniform(120_000, 280_000)
        annual_revenue = Decimal(round(employee_count * revenue_per_employee, -3))
        owner = rng.choice(reps)
        created_at = datetime.combine(
            today - timedelta(days=rng.randint(120, 730)), time(9, 0)
        )

        accounts.append(
            Account(
                name=name,
                industry=SUFFIX_INDUSTRY[suffix],
                employee_count=employee_count,
                annual_revenue=annual_revenue,
                region=_broad_region(owner.region),
                website=f"https://{_slug(name)}.example",
                owner=owner,
                created_at=created_at,
            )
        )
    return accounts


def _make_contacts(rng, accounts: list[Account]) -> list[Contact]:
    contacts = []
    for account in accounts:
        domain = account.website.removeprefix("https://")
        for _ in range(rng.randint(1, 4)):
            first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
            contacts.append(
                Contact(
                    account=account,
                    first_name=first,
                    last_name=last,
                    email=f"{first.lower()}.{last.lower()}@{domain}",
                    title=rng.choice(CONTACT_TITLES),
                    phone=f"+1-555-{rng.randint(0, 9999):04d}",
                )
            )
    return contacts


def _amounts_to_target(rng, target: Decimal, min_count: int = 1) -> list[Decimal]:
    amounts: list[Decimal] = []
    total = Decimal("0")
    while total < target or len(amounts) < min_count:
        amount = Decimal(rng.choice(ROUND_AMOUNTS))
        amounts.append(amount)
        total += amount
    return amounts


def _random_date(rng, start: date, end: date) -> date:
    span = (end - start).days
    return start if span <= 0 else start + timedelta(days=rng.randint(0, span))


def _deal_name(rng, account: Account) -> str:
    return f"{account.name} - {rng.choice(DEAL_SUFFIXES)}"


def _open_stage_for_close(rng, days_until_close: int) -> str:
    if days_until_close <= 14:
        return rng.choices(["negotiation", "proposal"], weights=[70, 30])[0]
    if days_until_close <= 45:
        return rng.choices(["proposal", "qualification"], weights=[60, 40])[0]
    return rng.choices(["qualification", "prospecting"], weights=[55, 45])[0]


def _early_open_stage(rng) -> str:
    return rng.choices(OPEN_STAGES, weights=[55, 30, 10, 5])[0]


def _month_end(year: int, month: int) -> date:
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return next_month - timedelta(days=1)


def _quarter_bounds(year: int, q: int) -> tuple[date, date]:
    start_month = (q - 1) * 3 + 1
    return date(year, start_month, 1), _month_end(year, start_month + 2)


def _shift_quarter(year: int, q: int, delta: int) -> tuple[int, int]:
    index = year * 4 + (q - 1) + delta
    return index // 4, index % 4 + 1


def _deal(rng, rep: Rep, accounts: list[Account], stage: str, amount: Decimal, close_date: date):
    account = rng.choice(accounts)
    return Opportunity(
        account=account,
        owner=rep,
        name=_deal_name(rng, account),
        amount=amount,
        stage=stage,
        probability=STAGE_PROBABILITY[stage],
        close_date=close_date,
    )


def _past_quarter_deals(
    rng, rep: Rep, accounts: list[Account], year: int, q: int
) -> list[Opportunity]:
    start, end = _quarter_bounds(year, q)
    target = rep.quarterly_quota * _pct(rng, 0.80, 1.15)

    deals = [
        _deal(rng, rep, accounts, "closed_won", amount, _random_date(rng, start, end))
        for amount in _amounts_to_target(rng, target)
    ]
    for _ in range(rng.randint(1, 3)):
        amount = Decimal(rng.choice(ROUND_AMOUNTS))
        close_date = _random_date(rng, start, end)
        deals.append(_deal(rng, rep, accounts, "closed_lost", amount, close_date))
    return deals


def _current_quarter_won(rng, rep: Rep, accounts: list[Account], today: date, year: int, q: int):
    start, end = _quarter_bounds(year, q)
    elapsed = (today - start).days + 1
    span = (end - start).days + 1
    performance = _pct(rng, 0.55, 1.00)
    target = rep.quarterly_quota * Decimal(elapsed) / Decimal(span) * performance

    deals = [
        _deal(rng, rep, accounts, "closed_won", amount, _random_date(rng, start, today))
        for amount in _amounts_to_target(rng, target)
    ]
    for _ in range(rng.randint(0, 2)):
        amount = Decimal(rng.choice(ROUND_AMOUNTS))
        close_date = _random_date(rng, start, today)
        deals.append(_deal(rng, rep, accounts, "closed_lost", amount, close_date))
    return deals


def _current_quarter_open(rng, rep: Rep, accounts: list[Account], today: date, year: int, q: int):
    start, end = _quarter_bounds(year, q)
    tomorrow = today + timedelta(days=1)
    if tomorrow > end:
        return []

    remaining_fraction = Decimal((end - today).days) / Decimal((end - start).days + 1)
    target = rep.quarterly_quota * remaining_fraction * _pct(rng, 0.90, 1.30)

    deals = []
    for amount in _amounts_to_target(rng, target):
        close_date = _random_date(rng, tomorrow, end)
        stage = _open_stage_for_close(rng, (close_date - today).days)
        deals.append(_deal(rng, rep, accounts, stage, amount, close_date))
    return deals


def _next_quarter_deals(rng, rep: Rep, accounts: list[Account], year: int, q: int):
    start, end = _quarter_bounds(year, q)
    target = rep.quarterly_quota * Decimal(2)

    deals = []
    for amount in _amounts_to_target(rng, target):
        close_date = _random_date(rng, start, end)
        stage = _early_open_stage(rng)
        deals.append(_deal(rng, rep, accounts, stage, amount, close_date))
    return deals


def _make_opportunities(
    rng, reps: list[Rep], accounts: list[Account], today: date
) -> list[Opportunity]:
    year, q = today.year, (today.month - 1) // 3 + 1
    opportunities: list[Opportunity] = []
    for rep in reps:
        opportunities += _past_quarter_deals(rng, rep, accounts, *_shift_quarter(year, q, -2))
        opportunities += _past_quarter_deals(rng, rep, accounts, *_shift_quarter(year, q, -1))
        opportunities += _current_quarter_won(rng, rep, accounts, today, year, q)
        opportunities += _current_quarter_open(rng, rep, accounts, today, year, q)
        opportunities += _next_quarter_deals(rng, rep, accounts, *_shift_quarter(year, q, 1))
    return opportunities


_LEAD_SCORE_RANGES = {
    "new": (0, 40),
    "working": (20, 60),
    "qualified": (60, 95),
    "disqualified": (0, 30),
}


def _lead_status_and_score(rng, days_ago: int) -> tuple[str, int]:
    if days_ago < 14:
        status = rng.choices(["new", "working"], weights=[85, 15])[0]
    elif days_ago < 45:
        status = rng.choices(
            ["new", "working", "qualified", "disqualified"], weights=[10, 35, 35, 20]
        )[0]
    else:
        status = rng.choices(["working", "qualified", "disqualified"], weights=[15, 35, 50])[0]
    return status, rng.randint(*_LEAD_SCORE_RANGES[status])


def _make_leads(rng, reps: list[Rep], today: date) -> list[Lead]:
    leads = []
    for _ in range(220):
        days_ago = rng.randint(0, 90)
        created_at = datetime.combine(today - timedelta(days=days_ago), time(rng.randint(8, 17), 0))
        status, score = _lead_status_and_score(rng, days_ago)

        first, last = rng.choice(FIRST_NAMES), rng.choice(LAST_NAMES)
        adjective, noun = rng.choice(ADJECTIVES), rng.choice(NOUNS)
        suffix = rng.choice(list(SUFFIX_INDUSTRY))
        company = f"{adjective} {noun} {suffix}"
        source = rng.choices(LEAD_SOURCES, weights=LEAD_SOURCE_WEIGHTS)[0]
        owner = rng.choice(reps) if rng.random() < 0.7 else None

        leads.append(
            Lead(
                first_name=first,
                last_name=last,
                email=f"{first.lower()}.{last.lower()}@{_slug(company)}.example",
                company=company,
                title=rng.choice(LEAD_TITLES),
                source=source,
                status=status,
                score=score,
                owner=owner,
                created_at=created_at,
            )
        )
    return leads


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seed the PragMattie Sync CRM database.")
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--if-empty", action="store_true", help="Seed only if the database has no reps yet."
    )
    group.add_argument(
        "--reset", action="store_true", help="Delete all CRM data, then seed fresh."
    )
    args = parser.parse_args(argv)
    mode = "reset" if args.reset else "if_empty" if args.if_empty else "default"

    try:
        counts = seed_database(get_engine(), mode=mode, today=date.today())
    except SeedError as exc:
        print(exc, file=sys.stderr)
        return 1

    if counts is None:
        print("Database already has reps; skipped (--if-empty).")
    else:
        print(counts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
