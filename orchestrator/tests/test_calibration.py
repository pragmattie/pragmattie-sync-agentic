from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sdlc import calibration
from sdlc.db import Base
from sdlc.tables import PullRequest
from sdlc.tiers import load_policy

THURSDAY = datetime(2026, 10, 1, 10, 0)

# With no module, no author and no CI history, a PR's score depends only on its own fields:
# author_record is 5 (unknown author), and timing is 0 (merged on a Thursday morning).
PLAIN = {}  # score 5, T0
BIG = {"additions": 900}  # 20 size + 10 no tests + 5 one review on 400+ lines + 5 = 40, T1
MEDIUM = {"additions": 100}  # 5 size + 10 no tests + 5 = 20, T1
MIGRATION = {"touches_migration": True}  # 15 + 5 = 20, T1 band, T3 by floor

# number: (fields, caused an incident)
HISTORY = {
    1: (PLAIN, False),
    2: (BIG, True),
    3: (MIGRATION, True),
    4: (PLAIN, False),
    5: (BIG, False),
    6: (PLAIN, True),
    7: (PLAIN, False),
    8: (PLAIN, False),
    9: (BIG, True),
    10: (MEDIUM, False),
    11: (PLAIN, False),
    12: (PLAIN, False),
}


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _pr(db, number, fields, incident, merged=True):
    db.add(
        PullRequest(
            number=number,
            title=f"PR {number}",
            review_count=1,
            state="merged" if merged else "open",
            created_at=THURSDAY - timedelta(days=1),
            merged_at=THURSDAY if merged else None,
            caused_incident=incident,
            **fields,
        )
    )


def _build(db, history):
    for number in reversed(list(history)):  # inserted out of order on purpose
        fields, incident = history[number]
        _pr(db, number, fields, incident)
    db.flush()


def test_score_history_scores_and_tiers_every_merged_pr_in_number_order(session):
    _build(session, HISTORY)
    _pr(session, 13, BIG, True, merged=False)
    session.flush()

    rows = calibration.score_history(session, load_policy())

    assert [row[0] for row in rows] == list(range(1, 13))
    assert rows[0] == (1, 5, "T0", False)
    assert rows[1] == (2, 40, "T1", True)
    assert rows[2] == (3, 20, "T3", True)
    assert rows[9] == (10, 20, "T1", False)


def test_calibrate_counts_per_tier_and_overall(session):
    _build(session, HISTORY)

    report = calibration.calibrate(session, load_policy())

    assert report["merged_prs"] == 12
    assert report["incident_prs"] == 4
    assert report["by_tier"] == {
        "T0": {"prs": 7, "incidents": 1},
        "T1": {"prs": 4, "incidents": 2},
        "T2": {"prs": 0, "incidents": 0},
        "T3": {"prs": 1, "incidents": 1},
    }


def test_top_decile_takes_ceil_n_over_ten_and_breaks_ties_by_number():
    # PRs 2, 5 and 9 all score 40 and only 2 is an incident. The decile of two must take 2 and 5;
    # any other tie-break (by input order, or by number descending) takes 9 or misses 2.
    rows = [(number, 5, "T0", False) for number in (1, 4, 7, 8, 11, 12)]
    rows += [(2, 40, "T1", True), (5, 40, "T1", False), (9, 40, "T1", False)]
    rows += [(3, 20, "T3", True), (6, 5, "T0", True), (10, 20, "T1", False)]
    tied_first = [(9, 40, "T1", False), (5, 40, "T1", False)]
    shuffled = tied_first + [row for row in rows if row not in tied_first][::-1]

    report = calibration.summarize(shuffled)

    assert report["top_decile"] == {"size": 2, "incidents": 1, "capture": 1 / 3}


def test_thresholds_give_precision_and_recall_at_each_tier_or_above(session):
    _build(session, HISTORY)

    thresholds = calibration.calibrate(session, load_policy())["thresholds"]

    assert thresholds["T1"] == {"flagged": 5, "incidents": 3, "precision": 0.6, "recall": 0.75}
    assert thresholds["T2"] == {"flagged": 1, "incidents": 1, "precision": 1.0, "recall": 0.25}
    assert thresholds["T3"] == thresholds["T2"]


def test_bars_fail_on_a_t0_incident_and_a_weak_top_decile(session):
    _build(session, HISTORY)

    bars = calibration.calibrate(session, load_policy())["bars"]

    assert bars == {"t0_has_no_incidents": False, "top_decile_captures_majority": False}


def test_bars_pass_when_the_top_pr_holds_the_only_incident(session):
    history = {number: (PLAIN, False) for number in range(1, 10)}
    history[10] = (BIG, True)
    _build(session, history)

    report = calibration.calibrate(session, load_policy())

    assert report["top_decile"] == {"size": 1, "incidents": 1, "capture": 1.0}
    assert report["bars"] == {"t0_has_no_incidents": True, "top_decile_captures_majority": True}
    assert "FAIL" not in calibration.format_report(report)


def test_an_empty_history_has_no_ratios():
    report = calibration.summarize([])

    assert report["top_decile"] == {"size": 0, "incidents": 0, "capture": None}
    assert report["thresholds"]["T1"]["precision"] is None
    assert report["bars"]["top_decile_captures_majority"] is False
    assert "n/a" in calibration.format_report(report)


def test_pool_sums_counts_and_rates_across_histories():
    first = calibration.summarize(
        [(1, 90, "T3", True), (2, 10, "T0", False), (3, 30, "T1", False), (4, 5, "T0", False)]
    )
    second = calibration.summarize(
        [(1, 60, "T2", False), (2, 50, "T2", True), (3, 5, "T0", True), (4, 5, "T0", False)]
    )

    pooled = calibration.pool([first, second])

    assert pooled["histories"] == 2
    assert pooled["merged_prs"] == 8
    assert pooled["incident_prs"] == 3
    assert pooled["by_tier"]["T0"] == {"prs": 4, "incidents": 1}
    assert pooled["overall_rate"] == pytest.approx(3 / 8)
    assert pooled["t0_rate"] == pytest.approx(1 / 4)
    assert pooled["top_decile"] == {
        "size": 2,
        "incidents": 1,
        "capture": pytest.approx(1 / 3),
        "lowest": 0.0,
        "highest": 1.0,
    }
    assert pooled["histories_without_t0_incident"] == 1
    assert pooled["thresholds"]["T2"] == {
        "flagged": 3,
        "incidents": 2,
        "precision": pytest.approx(2 / 3),
        "recall": pytest.approx(2 / 3),
    }
    # T0 rate 25% > 25% of 37.5%; capture 33% is not more than half.
    assert pooled["bars"] == {
        "t0_rate_at_most_a_quarter_of_overall": False,
        "top_decile_captures_majority_pooled": False,
    }


def test_pool_bars_pass_with_a_clean_t0_and_a_strong_decile():
    run = calibration.summarize([(1, 90, "T3", True)] + [(n, 5, "T0", False) for n in range(2, 11)])

    pooled = calibration.pool([run, run])

    assert pooled["bars"] == {
        "t0_rate_at_most_a_quarter_of_overall": True,
        "top_decile_captures_majority_pooled": True,
    }


def test_format_many_ends_with_both_bars():
    failing = calibration.pool([calibration.summarize([(1, 5, "T0", True)])])
    passing = calibration.pool([calibration.summarize([(1, 90, "T3", True), (2, 5, "T0", False)])])

    failing_lines = calibration.format_many(failing).splitlines()
    passing_lines = calibration.format_many(passing).splitlines()

    assert failing_lines[-2].startswith("Bar 1") and failing_lines[-2].endswith("FAIL")
    assert failing_lines[-1].startswith("Bar 2") and failing_lines[-1].endswith("PASS")
    assert passing_lines[-2].endswith("PASS") and passing_lines[-1].endswith("PASS")


def test_the_pooled_bars_hold_on_thirty_generated_histories():
    """Locked: a weight change that breaks either pooled bar fails this test."""
    report = calibration.calibrate_many(load_policy(), histories=30)

    assert report["histories"] == 30
    assert report["incident_prs"] > 300
    assert report["bars"]["t0_rate_at_most_a_quarter_of_overall"]
    assert report["bars"]["top_decile_captures_majority_pooled"]
    assert "FAIL" not in calibration.format_many(report)

    pooled = calibration.POOLED_RESULT
    assert report["merged_prs"] == pooled["merged_prs"]
    assert report["incident_prs"] == pooled["incident_prs"]
    assert round(report["overall_rate"], 4) == pooled["overall_rate"]
    assert round(report["t0_rate"], 4) == pooled["t0_rate"]
    assert round(report["top_decile"]["capture"], 4) == pooled["top_decile"]["capture"]
    assert report["top_decile"]["lowest"] == pooled["top_decile"]["lowest"]
    assert report["top_decile"]["highest"] == pooled["top_decile"]["highest"]
    assert report["bars"] == pooled["bars"]


def _merged(db, number, source, incident=False):
    db.add(
        PullRequest(
            number=number,
            title=f"PR {number}",
            source=source,
            review_count=1,
            state="merged",
            created_at=THURSDAY - timedelta(days=1),
            merged_at=THURSDAY,
            caused_incident=incident,
        )
    )


def test_calibrate_can_be_limited_to_one_source(session):
    _merged(session, 1, "synthetic", incident=True)
    _merged(session, 2, "synthetic")
    _merged(session, 3, "github")
    session.flush()

    assert calibration.calibrate(session, load_policy())["merged_prs"] == 3
    synthetic = calibration.calibrate(session, load_policy(), source="synthetic")
    assert synthetic["merged_prs"] == 2
    assert synthetic["incident_prs"] == 1
    assert [row[0] for row in calibration.score_history(session, load_policy(), "github")] == [3]
