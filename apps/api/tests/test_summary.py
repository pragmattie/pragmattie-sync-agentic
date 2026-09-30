from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.summary import build_summary

QUARTER_START = date(2026, 7, 1)
QUARTER_END = date(2026, 9, 30)


@dataclass
class LeadRow:
    status: str


@dataclass
class DealRow:
    amount: Decimal
    stage: str
    close_date: date


class TestBuildSummary:
    def test_leads_by_status_counts_each_status_present(self):
        leads = [
            LeadRow("new"),
            LeadRow("new"),
            LeadRow("working"),
            LeadRow("qualified"),
            LeadRow("disqualified"),
            LeadRow("converted"),
        ]

        summary = build_summary(leads, [], QUARTER_START, QUARTER_END)

        assert summary["leads_by_status"] == {
            "new": 2,
            "working": 1,
            "qualified": 1,
            "disqualified": 1,
            "converted": 1,
        }

    def test_statuses_with_no_leads_are_left_out(self):
        summary = build_summary([LeadRow("new")], [], QUARTER_START, QUARTER_END)

        assert summary["leads_by_status"] == {"new": 1}

    def test_open_leads_counts_new_working_and_qualified_only(self):
        leads = [
            LeadRow("new"),
            LeadRow("working"),
            LeadRow("qualified"),
            LeadRow("disqualified"),
            LeadRow("converted"),
        ]

        summary = build_summary(leads, [], QUARTER_START, QUARTER_END)

        assert summary["open_leads"] == 3

    def test_open_pipeline_and_open_deals_span_all_time(self):
        deals = [
            DealRow(Decimal("1000.00"), "prospecting", date(2020, 1, 1)),
            DealRow(Decimal("2000.00"), "negotiation", date(2030, 12, 31)),
            DealRow(Decimal("500.00"), "closed_won", date(2026, 8, 1)),
            DealRow(Decimal("999.00"), "closed_lost", date(2026, 8, 1)),
        ]

        summary = build_summary([], deals, QUARTER_START, QUARTER_END)

        assert summary["open_pipeline"] == Decimal("3000.00")
        assert summary["open_deals"] == 2

    def test_won_this_quarter_only_counts_closed_won_deals_closing_in_the_quarter(self):
        deals = [
            DealRow(Decimal("10000.00"), "closed_won", date(2026, 8, 15)),
            DealRow(Decimal("99999.00"), "closed_won", date(2026, 6, 30)),
            DealRow(Decimal("50000.00"), "negotiation", date(2026, 8, 15)),
        ]

        summary = build_summary([], deals, QUARTER_START, QUARTER_END)

        assert summary["won_this_quarter"] == Decimal("10000.00")

    def test_empty_input_is_all_zeros(self):
        summary = build_summary([], [], QUARTER_START, QUARTER_END)

        assert summary["leads_by_status"] == {}
        assert summary["open_leads"] == 0
        assert summary["open_pipeline"] == Decimal("0.00")
        assert summary["open_deals"] == 0
        assert summary["won_this_quarter"] == Decimal("0.00")
