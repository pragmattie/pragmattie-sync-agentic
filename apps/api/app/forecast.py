"""Sales forecast calculations, kept independent of the database and HTTP layers.

Callers pass in plain rows for reps and opportunities (anything with the right
attributes works, including the SQLAlchemy models themselves), which keeps this
module testable without a database.
"""

import re
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.stages import OPEN_STAGES

_QUARTER_RE = re.compile(r"^(\d{4})-Q([1-4])$")
_TWO_PLACES = Decimal("0.01")
_ONE_PLACE = Decimal("0.1")


def parse_quarter(quarter: str | None, today: date) -> tuple[str, date, date]:
    """Return (quarter label, start date, end date) for a "YYYY-Qn" string.

    `quarter=None` defaults to the quarter containing `today`. Raises `ValueError`
    with the endpoint's exact 400 message on anything that isn't shaped like
    "2026-Q3".
    """
    if quarter is None:
        year = today.year
        q = (today.month - 1) // 3 + 1
    else:
        match = _QUARTER_RE.match(quarter)
        if not match:
            raise ValueError("quarter must look like 2026-Q3")
        year, q = int(match.group(1)), int(match.group(2))

    start_month = (q - 1) * 3 + 1
    start = date(year, start_month, 1)
    end = _month_end(year, start_month + 2)
    return f"{year}-Q{q}", start, end


def build_forecast(reps: list[Any], opportunities: list[Any], start: date, end: date) -> dict:
    """Compute the forecast for the quarter spanning `start` to `end`."""
    in_quarter = [o for o in opportunities if start <= o.close_date <= end]

    won, commit, best_case, weighted = _category_totals(in_quarter)
    pipeline = _sum(o.amount for o in in_quarter if o.stage in OPEN_STAGES)

    return {
        "quota": _money(_sum(r.quarterly_quota for r in reps)),
        "won": _money(won),
        "commit": _money(commit),
        "best_case": _money(best_case),
        "pipeline": _money(pipeline),
        "weighted": _money(weighted),
        "by_month": _by_month(in_quarter, start),
        "by_rep": _by_rep(reps, in_quarter),
        "by_stage": _by_stage(in_quarter),
    }


def _month_end(year: int, month: int) -> date:
    year, month = year + (month - 1) // 12, (month - 1) % 12 + 1
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return next_month - timedelta(days=1)


def _sum(values: Any) -> Decimal:
    total = Decimal("0.00")
    for value in values:
        total += value
    return total


def _weighted_open(deals: list[Any]) -> Decimal:
    return _sum(
        deal.amount * deal.probability / Decimal(100) for deal in deals if deal.stage in OPEN_STAGES
    )


def _money(value: Decimal) -> Decimal:
    return value.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


def _category_totals(deals: list[Any]) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    won = _sum(d.amount for d in deals if d.stage == "closed_won")
    negotiation = _sum(d.amount for d in deals if d.stage == "negotiation")
    proposal = _sum(d.amount for d in deals if d.stage == "proposal")
    commit = won + negotiation
    best_case = commit + proposal
    weighted = won + _weighted_open(deals)
    return won, commit, best_case, weighted


def _by_month(deals: list[Any], start: date) -> list[dict]:
    months = []
    month_start = date(start.year, start.month, 1)
    for _ in range(3):
        month_deals = [
            d
            for d in deals
            if d.close_date.year == month_start.year and d.close_date.month == month_start.month
        ]
        won, commit, best_case, weighted = _category_totals(month_deals)
        months.append(
            {
                "month": f"{month_start.year:04d}-{month_start.month:02d}",
                "won": _money(won),
                "commit": _money(commit),
                "best_case": _money(best_case),
                "weighted": _money(weighted),
            }
        )
        month_start = _month_end(month_start.year, month_start.month) + timedelta(days=1)
    return months


def _by_rep(reps: list[Any], deals: list[Any]) -> list[dict]:
    deals_by_owner: dict[int, list[Any]] = defaultdict(list)
    for deal in deals:
        if deal.owner_id is not None:
            deals_by_owner[deal.owner_id].append(deal)

    rows = []
    for rep in reps:
        rep_deals = deals_by_owner.get(rep.id, [])
        if not rep_deals:
            continue  # nothing to report for a rep without deals in the quarter
        won, commit, _best_case, weighted = _category_totals(rep_deals)
        rows.append(
            {
                "rep": {"id": rep.id, "name": rep.name},
                "quota": _money(rep.quarterly_quota),
                "won": _money(won),
                "commit": _money(commit),
                "weighted": _money(weighted),
                "attainment_pct": _attainment_pct(won, rep.quarterly_quota),
                "_sort_key": weighted,
            }
        )
    rows.sort(key=lambda row: row["_sort_key"], reverse=True)
    for row in rows:
        del row["_sort_key"]
    return rows


def _attainment_pct(won: Decimal, quota: Decimal) -> float:
    if quota == 0:
        return 0.0
    return float((won / quota * 100).quantize(_ONE_PLACE, rounding=ROUND_HALF_UP))


def _by_stage(deals: list[Any]) -> list[dict]:
    result = []
    for stage in OPEN_STAGES:
        stage_deals = [d for d in deals if d.stage == stage]
        result.append(
            {
                "stage": stage,
                "count": len(stage_deals),
                "amount": _money(_sum(d.amount for d in stage_deals)),
            }
        )
    return result
