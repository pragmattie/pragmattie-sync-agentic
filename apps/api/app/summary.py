"""Dashboard summary calculations, kept independent of the database and HTTP layers.

Callers pass in plain rows for leads and opportunities (anything with the right
attributes works, including the SQLAlchemy models themselves), which keeps this
module testable without a database.
"""

from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.stages import OPEN_STAGES

_TWO_PLACES = Decimal("0.01")

OPEN_LEAD_STATUSES = ("new", "working", "qualified")


def build_summary(leads: list[Any], opportunities: list[Any], start: date, end: date) -> dict:
    """Compute the dashboard summary.

    `start` and `end` bound the current quarter, used only for `won_this_quarter`;
    every other figure spans all time.
    """
    leads_by_status: dict[str, int] = defaultdict(int)
    for lead in leads:
        leads_by_status[lead.status] += 1

    open_leads = sum(
        count for status, count in leads_by_status.items() if status in OPEN_LEAD_STATUSES
    )

    open_deals = [o for o in opportunities if o.stage in OPEN_STAGES]
    won_this_quarter = _sum(
        o.amount for o in opportunities if o.stage == "closed_won" and start <= o.close_date <= end
    )

    return {
        "leads_by_status": dict(leads_by_status),
        "open_leads": open_leads,
        "open_pipeline": _money(_sum(o.amount for o in open_deals)),
        "open_deals": len(open_deals),
        "won_this_quarter": _money(won_this_quarter),
    }


def _sum(values: Any) -> Decimal:
    total = Decimal("0.00")
    for value in values:
        total += value
    return total


def _money(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
