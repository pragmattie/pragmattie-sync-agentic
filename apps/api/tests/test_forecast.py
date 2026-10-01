from dataclasses import dataclass
from datetime import date
from decimal import Decimal

import pytest

from app.forecast import build_forecast, parse_quarter


@dataclass
class Rep:
    id: int
    name: str
    quarterly_quota: Decimal


@dataclass
class Deal:
    amount: Decimal
    stage: str
    probability: int
    close_date: date
    owner_id: int | None = None


class TestParseQuarter:
    def test_defaults_to_the_quarter_containing_today(self):
        assert parse_quarter(None, date(2026, 8, 15)) == (
            "2026-Q3",
            date(2026, 7, 1),
            date(2026, 9, 30),
        )

    @pytest.mark.parametrize(
        ("today", "quarter", "start", "end"),
        [
            (date(2026, 1, 1), "2026-Q1", date(2026, 1, 1), date(2026, 3, 31)),
            (date(2026, 4, 30), "2026-Q2", date(2026, 4, 1), date(2026, 6, 30)),
            (date(2026, 10, 1), "2026-Q4", date(2026, 10, 1), date(2026, 12, 31)),
        ],
    )
    def test_each_quarter_spans_the_right_months(self, today, quarter, start, end):
        assert parse_quarter(None, today) == (quarter, start, end)

    def test_an_explicit_quarter_overrides_today(self):
        assert parse_quarter("2027-Q1", date(2026, 8, 15)) == (
            "2027-Q1",
            date(2027, 1, 1),
            date(2027, 3, 31),
        )

    @pytest.mark.parametrize(
        "quarter", ["2026", "2026-Q5", "2026-Q0", "26-Q3", "2026Q3", "2026-q3", "not-a-quarter", ""]
    )
    def test_rejects_anything_not_shaped_like_a_quarter(self, quarter):
        with pytest.raises(ValueError, match="quarter must look like 2026-Q3"):
            parse_quarter(quarter, date(2026, 1, 1))


QUARTER_START = date(2026, 7, 1)
QUARTER_END = date(2026, 9, 30)


class TestBuildForecast:
    def test_categories_on_a_hand_built_set_of_deals(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("100000.00"))]
        deals = [
            Deal(Decimal("10000.00"), "closed_won", 100, date(2026, 7, 10), owner_id=1),
            Deal(Decimal("5000.00"), "closed_lost", 0, date(2026, 7, 12), owner_id=1),
            Deal(Decimal("20000.00"), "negotiation", 75, date(2026, 8, 1), owner_id=1),
            Deal(Decimal("30000.00"), "proposal", 50, date(2026, 8, 15), owner_id=1),
            Deal(Decimal("15000.00"), "qualification", 25, date(2026, 9, 1), owner_id=1),
            Deal(Decimal("8000.00"), "prospecting", 10, date(2026, 9, 20), owner_id=1),
        ]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert forecast["won"] == Decimal("10000.00")
        assert forecast["commit"] == Decimal("30000.00")
        assert forecast["best_case"] == Decimal("60000.00")
        assert forecast["pipeline"] == Decimal("73000.00")
        # weighted = won + (20000*.75 + 30000*.50 + 15000*.25 + 8000*.10)
        assert forecast["weighted"] == Decimal("10000.00") + Decimal("34550.00")

    def test_weighted_is_rounded_half_up_to_two_places(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("100000.00"))]
        # 0.10 * 25% = 0.025 and 1001.01 * 10% = 100.101: together 100.126, so 100.13.
        deals = [
            Deal(Decimal("0.10"), "qualification", 25, date(2026, 7, 10), owner_id=1),
            Deal(Decimal("1001.01"), "prospecting", 10, date(2026, 7, 20), owner_id=1),
            Deal(Decimal("500.00"), "closed_won", 100, date(2026, 7, 25), owner_id=1),
        ]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert forecast["weighted"] == Decimal("600.13")
        assert forecast["by_month"][0]["weighted"] == Decimal("600.13")
        assert forecast["by_rep"][0]["weighted"] == Decimal("600.13")

    def test_closed_lost_deals_count_in_no_category(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("100000.00"))]
        # A probability left on a lost deal (PATCH can set one) still doesn't weight it.
        deals = [Deal(Decimal("9000.00"), "closed_lost", 30, date(2026, 8, 1), owner_id=1)]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        for category in ("won", "commit", "best_case", "pipeline", "weighted"):
            assert forecast[category] == Decimal("0.00"), category

    def test_deals_outside_the_quarter_do_not_count(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("0.00"))]
        deals = [
            Deal(Decimal("10000.00"), "closed_won", 100, date(2026, 6, 30), owner_id=1),
            Deal(Decimal("20000.00"), "closed_won", 100, date(2026, 10, 1), owner_id=1),
        ]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert forecast["won"] == Decimal("0.00")
        assert forecast["by_stage"] == [
            {"stage": "prospecting", "count": 0, "amount": Decimal("0.00")},
            {"stage": "qualification", "count": 0, "amount": Decimal("0.00")},
            {"stage": "proposal", "count": 0, "amount": Decimal("0.00")},
            {"stage": "negotiation", "count": 0, "amount": Decimal("0.00")},
        ]

    def test_unowned_deals_count_in_totals_but_not_under_any_rep(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("50000.00"))]
        deals = [
            Deal(Decimal("10000.00"), "closed_won", 100, date(2026, 7, 10), owner_id=None),
        ]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert forecast["won"] == Decimal("10000.00")
        assert forecast["by_rep"] == [
            {
                "rep": {"id": 1, "name": "Zoe Park"},
                "quota": Decimal("50000.00"),
                "won": Decimal("0.00"),
                "commit": Decimal("0.00"),
                "weighted": Decimal("0.00"),
                "attainment_pct": 0.0,
            }
        ]

    def test_a_rep_with_zero_quota_has_zero_attainment(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("0.00"))]
        deals = [Deal(Decimal("10000.00"), "closed_won", 100, date(2026, 7, 10), owner_id=1)]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert forecast["by_rep"][0]["attainment_pct"] == 0.0

    def test_reps_with_no_deals_are_still_listed(self):
        reps = [
            Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("50000.00")),
            Rep(id=2, name="Avery Cole", quarterly_quota=Decimal("40000.00")),
        ]

        forecast = build_forecast(reps, [], QUARTER_START, QUARTER_END)

        assert [row["rep"]["name"] for row in forecast["by_rep"]] in (
            ["Zoe Park", "Avery Cole"],
            ["Avery Cole", "Zoe Park"],
        )
        assert all(row["won"] == Decimal("0.00") for row in forecast["by_rep"])

    def test_by_rep_is_sorted_by_weighted_descending(self):
        reps = [
            Rep(id=1, name="Low", quarterly_quota=Decimal("10000.00")),
            Rep(id=2, name="High", quarterly_quota=Decimal("10000.00")),
            Rep(id=3, name="Mid", quarterly_quota=Decimal("10000.00")),
        ]
        deals = [
            Deal(Decimal("1000.00"), "closed_won", 100, date(2026, 7, 5), owner_id=1),
            Deal(Decimal("9000.00"), "closed_won", 100, date(2026, 7, 5), owner_id=2),
            Deal(Decimal("5000.00"), "closed_won", 100, date(2026, 7, 5), owner_id=3),
        ]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert [row["rep"]["name"] for row in forecast["by_rep"]] == ["High", "Mid", "Low"]

    def test_attainment_pct_is_won_over_quota_rounded_to_one_decimal(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("30000.00"))]
        deals = [Deal(Decimal("10001.00"), "closed_won", 100, date(2026, 7, 5), owner_id=1)]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert forecast["by_rep"][0]["attainment_pct"] == 33.3

    def test_quota_is_the_sum_of_every_reps_quota(self):
        reps = [
            Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("50000.00")),
            Rep(id=2, name="Avery Cole", quarterly_quota=Decimal("40000.00")),
        ]

        forecast = build_forecast(reps, [], QUARTER_START, QUARTER_END)

        assert forecast["quota"] == Decimal("90000.00")

    def test_by_month_splits_the_quarter_into_its_three_months(self):
        reps = [Rep(id=1, name="Zoe Park", quarterly_quota=Decimal("0.00"))]
        deals = [
            Deal(Decimal("1000.00"), "closed_won", 100, date(2026, 7, 15), owner_id=1),
            Deal(Decimal("2000.00"), "negotiation", 75, date(2026, 8, 20), owner_id=1),
            Deal(Decimal("3000.00"), "proposal", 50, date(2026, 9, 1), owner_id=1),
        ]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        months = [month["month"] for month in forecast["by_month"]]
        assert months == ["2026-07", "2026-08", "2026-09"]
        assert forecast["by_month"][0]["won"] == Decimal("1000.00")
        assert forecast["by_month"][1]["commit"] == Decimal("2000.00")
        assert forecast["by_month"][2]["best_case"] == Decimal("3000.00")
        assert forecast["by_month"][2]["weighted"] == Decimal("1500.00")

    def test_by_stage_lists_open_stages_in_pipeline_order(self):
        reps = []
        deals = [
            Deal(Decimal("1000.00"), "negotiation", 75, date(2026, 7, 1)),
            Deal(Decimal("2000.00"), "prospecting", 10, date(2026, 7, 2)),
            Deal(Decimal("500.00"), "prospecting", 10, date(2026, 7, 3)),
        ]

        forecast = build_forecast(reps, deals, QUARTER_START, QUARTER_END)

        assert [row["stage"] for row in forecast["by_stage"]] == [
            "prospecting",
            "qualification",
            "proposal",
            "negotiation",
        ]
        assert forecast["by_stage"][0] == {
            "stage": "prospecting",
            "count": 2,
            "amount": Decimal("2500.00"),
        }
        assert forecast["by_stage"][3] == {
            "stage": "negotiation",
            "count": 1,
            "amount": Decimal("1000.00"),
        }

    def test_empty_forecast_is_all_zeros(self):
        forecast = build_forecast([], [], QUARTER_START, QUARTER_END)

        for field in ("quota", "won", "commit", "best_case", "pipeline", "weighted"):
            assert forecast[field] == Decimal("0.00")
        assert forecast["by_rep"] == []
        assert len(forecast["by_month"]) == 3
        assert len(forecast["by_stage"]) == 4
