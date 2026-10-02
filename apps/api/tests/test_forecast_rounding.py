from datetime import date
from decimal import Decimal
from types import SimpleNamespace

from app.forecast import build_forecast


def test_attainment_rounds_half_up_to_one_decimal():
    rep = SimpleNamespace(id=1, name="Zoe Park", quarterly_quota=Decimal("8000.00"))
    deal = SimpleNamespace(
        amount=Decimal("996.00"),
        stage="closed_won",
        probability=100,
        close_date=date(2026, 8, 15),
        owner_id=1,
    )

    forecast = build_forecast([rep], [deal], date(2026, 7, 1), date(2026, 9, 30))

    # 996 / 8000 = 12.45%: half up gives 12.5, half-even would give 12.4.
    assert forecast["by_rep"][0]["attainment_pct"] == 12.5
