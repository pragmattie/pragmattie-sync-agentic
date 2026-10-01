"""Dashboard summary calculations.

`query_summary` is what the endpoint uses: it computes every figure in the database
with `COUNT`, `SUM` and `GROUP BY`, so no rows are loaded into memory. `build_summary`
is the same calculation over plain rows (anything with the right attributes works),
kept as the reference that the SQL version is tested against without a database.
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Lead, Opportunity
from app.stages import OPEN_STAGES

_TWO_PLACES = Decimal("0.01")

OPEN_LEAD_STATUSES = ("new", "working", "qualified")


def query_summary(session: Session, start: date, end: date) -> dict:
    """Compute the dashboard summary in the database.

    `start` and `end` bound the current quarter, used only for `won_this_quarter`;
    every other figure spans all time.
    """
    leads_by_status = {
        status: count
        for status, count in session.execute(
            select(Lead.status, func.count(Lead.id)).group_by(Lead.status)
        )
    }

    open_pipeline, open_deals = session.execute(
        select(func.coalesce(func.sum(Opportunity.amount), 0), func.count(Opportunity.id)).where(
            Opportunity.stage.in_(OPEN_STAGES)
        )
    ).one()

    won_this_quarter = session.scalar(
        select(func.coalesce(func.sum(Opportunity.amount), 0)).where(
            Opportunity.stage == "closed_won",
            Opportunity.close_date >= start,
            Opportunity.close_date <= end,
        )
    )

    return _summary(leads_by_status, Decimal(open_pipeline), open_deals, Decimal(won_this_quarter))


def build_summary(leads: list[Any], opportunities: list[Any], start: date, end: date) -> dict:
    """Compute the dashboard summary from plain rows (see `query_summary`)."""
    leads_by_status: dict[str, int] = defaultdict(int)
    for lead in leads:
        leads_by_status[lead.status] += 1

    open_deals = [o for o in opportunities if o.stage in OPEN_STAGES]
    won_this_quarter = _sum(
        o.amount for o in opportunities if o.stage == "closed_won" and start <= o.close_date <= end
    )

    return _summary(
        dict(leads_by_status),
        _sum(o.amount for o in open_deals),
        len(open_deals),
        won_this_quarter,
    )


def _summary(
    leads_by_status: dict[str, int],
    open_pipeline: Decimal,
    open_deals: int,
    won_this_quarter: Decimal,
) -> dict:
    open_leads = sum(
        count for status, count in leads_by_status.items() if status in OPEN_LEAD_STATUSES
    )
    return {
        "leads_by_status": leads_by_status,
        "open_leads": open_leads,
        "open_pipeline": _money(open_pipeline),
        "open_deals": open_deals,
        "won_this_quarter": _money(won_this_quarter),
    }


def _sum(values: Any) -> Decimal:
    total = Decimal("0.00")
    for value in values:
        total += value
    return total


def _money(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
