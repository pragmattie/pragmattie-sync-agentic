from datetime import datetime
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc import compare_v1
from sdlc.compare_v1 import compare, main, v1_rows, v2_rows
from sdlc.db import Base
from sdlc.github_client import GitHubClient
from sdlc.tables import AgentDecision
from sdlc.v1_export import load_export

FIXTURES = Path(__file__).parent / "fixtures"
PASS = FIXTURES / "v1_export_pass.json"
FAIL = FIXTURES / "v1_export_fail.json"
REPO = "pragmattie/pragmattie-sync-agentic"
OBSERVING = datetime(2026, 10, 5, 9, 30)

# v2's own scores: (score, tier, floors) per PR. PR 11 v1 never scored; PR 12's run failed.
V2_RISK = {
    1: (10, "T0", []),
    2: (25, "T1", []),
    3: (30, "T1", []),
    4: (40, "T1", []),
    5: (45, "T1", []),
    6: (55, "T2", ["pipeline"]),
    7: (60, "T2", []),
    8: (85, "T3", ["migration"]),
    9: (15, "T0", []),
    10: (35, "T1", []),
    11: (20, "T1", []),
}
OK = {"conclusion": "success", "flaky": False, "seconds": 60}


def _sha(pr):
    return f"{pr:02d}" + "a" * 38


def _row(agent, subject_type, subject_id, sha, at, source="github", **fields):
    return AgentDecision(
        created_at=at,
        agent=agent,
        agent_version="v1",
        subject_type=subject_type,
        subject_source=source,
        subject_id=subject_id,
        head_sha=sha,
        trigger=fields.pop("trigger", "poll"),
        **fields,
    )


def _risk(pr, score, tier, floors, **fields):
    return _row(
        "pr_risk",
        "pr",
        pr,
        _sha(pr),
        datetime(2026, 10, 5, 8 + pr, 30),
        final_score=score,
        tier=tier,
        signals={"change_size": 10, "module_incidents": 5},
        inputs_digest={"additions": 100, "module_incident_rate": 0.1},
        output={"floors": floors},
        **fields,
    )


def _v2_rows():
    rows = [_risk(pr, *scored) for pr, scored in V2_RISK.items()]
    rows.append(_risk(12, 0, "T2", [], status="error", error="timeout"))
    # Ignored: a trial row, and a row imported from v1.
    rows.append(_risk(3, 99, "T3", [], trigger="trial", attempt=2))
    rows.append(_risk(4, 99, "T3", [], source="v1"))
    rows.append(_risk(13, 0, "T0", [], source="v1"))
    for pr, suites in ((1, ["api"]), (2, ["api", "crm-web"])):
        at = datetime(2026, 10, 5, 8 + pr, 31)
        reasons = {suite: f"code changed for {suite}" for suite in suites}
        output = {"suites": suites, "skipped": [], "reasons": reasons, "full": None}
        rows.append(_row("test_selector", "pr", pr, _sha(pr), at, output=output))
        at = datetime(2026, 10, 5, 8 + pr, 50)
        output = {"results": {"api": OK, "crm-web": OK}}
        rows.append(
            _row(
                "test_selector",
                "pr",
                pr,
                _sha(pr),
                at,
                trigger="ci_result",
                attempt=2,
                output=output,
            )
        )
    for number in (50, 51):
        output = {"module": "pipeline", "type": "feature", "priority": "P2", "estimate_points": 3}
        at = datetime(2026, 10, 6, 10)
        rows.append(_row("triage", "issue", number, f"h{number}" + "b" * 10, at, output=output))
    for sha, pr, tier in (("d1" + "c" * 38, 7, "T1"), ("d2" + "c" * 38, 8, "T2")):
        output = {"verdict": "pass" if tier == "T1" else "hold", "reasons": []}
        at = datetime(2026, 10, 7, 10)
        rows.append(_row("release_gate", "release", pr, sha, at, tier=tier, output=output))
    return rows


@pytest.fixture(autouse=True)
def repo(monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", REPO)


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        db.add_all(_v2_rows())
        db.commit()
    return sqlite_engine


def _count(engine):
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(AgentDecision))


def _report(engine, fixture=PASS, since=None):
    with Session(engine) as db:
        observing = compare_v1.observing_since(db)
        rows = v2_rows(db, since or observing)
    return compare(
        v1_rows(load_export(fixture)), rows, start=since or observing, observing=observing
    )


def test_v2_starts_observing_at_its_first_real_pr_risk_row(engine):
    with Session(engine) as db:
        assert compare_v1.observing_since(db) == OBSERVING


def test_equal_tiers_and_scores_agree_and_a_different_tier_shows_scores_and_signals(engine):
    risk = _report(engine).risk

    assert risk.compared == 10
    assert risk.agreed == {"tier": 9}
    tier_differences = [d for d in risk.differences if "same tier" not in d]
    assert tier_differences == [
        "PR #10 @ 10aaaaa: v1 T2 score 52, v2 T1 score 35; differs in: " "change_size 20 vs 10"
    ]
    assert not any(d.startswith("PR #1 @") for d in risk.differences)


def test_a_score_difference_within_the_same_tier_is_listed_but_agrees(engine):
    risk = _report(engine).risk

    assert "PR #2 @ 02aaaaa: v1 T1 score 27, v2 T1 score 25 (same tier)" in "\n".join(
        risk.differences
    )


def test_trial_rows_and_rows_imported_from_v1_are_ignored(engine):
    report = _report(engine)

    # PR 3's trial row (T3) and PR 4's v1-sourced row (T3) would have broken agreement.
    assert not any(d.startswith(("PR #3 ", "PR #4 ")) for d in report.risk.differences)
    # v1's own trial row on PR 1 (T3) is ignored too.
    assert not any(d.startswith("PR #1 ") for d in report.risk.differences)


def test_rows_on_one_side_only_are_counted_with_their_reason(engine):
    risk = _report(engine).risk

    assert risk.one_sided.counts == {("v2", None): 1, ("v1", "v2's run failed"): 1}


def test_v1_rows_before_v2_observed_are_counted_as_such_with_since(engine):
    risk = _report(engine, since=datetime(2026, 10, 1)).risk

    assert risk.one_sided.counts[("v1", compare_v1.NOT_OBSERVING)] == 1
    assert risk.compared == 10


def test_suites_differences_list_both_sides_reasons(engine):
    suites = _report(engine).suites

    assert suites.compared == 2
    assert suites.agreed == {"suites": 1}
    assert suites.differences == ["PR #2 @ 02aaaaa: v2 only crm-web (code changed for crm-web)"]
    assert suites.bugs == []


def test_a_ci_result_mismatch_is_reported_as_a_bug(engine):
    suites = _report(engine, FAIL).suites

    assert len(suites.bugs) == 1
    assert suites.bugs[0].startswith("PR #2 @ 02aaaaa: CI results differ (api ")


def test_triage_compares_each_dimension(engine):
    triage = _report(engine).triage

    assert triage.compared == 2
    assert triage.agreed == {"module": 2, "type": 2, "priority": 2, "points": 1}
    assert triage.differences == ["issue #51 (h51bbbbbbbbb): points 5 vs 3"]


def test_release_verdicts_compare_per_deploy_commit(engine):
    passing, failing = _report(engine).gate, _report(engine, FAIL).gate

    assert passing.compared == 2
    assert passing.agreed == {"verdict": 2}
    assert failing.agreed == {"verdict": 1}
    assert failing.differences == [
        "deploy d2ccccc: v1 pass at T2 (T2 release), v2 hold at T2 (no reasons)"
    ]


def test_the_passing_fixture_meets_the_bar(engine):
    bar = _report(engine).bar

    assert bar.tier_agreement == 0.9
    assert bar.floored_lower == ()
    assert bar.verdict_agreement == 1.0
    assert bar.met


def test_a_lower_v2_tier_on_a_floored_pr_fails_the_bar(engine):
    bar = _report(engine, FAIL).bar

    assert bar.tier_agreement == 0.7
    assert bar.floored_lower == ("PR #6 @ 06aaaaa: v1 T3, v2 T2 (floors: pipeline)",)
    assert bar.verdict_agreement == 0.5
    assert not (bar.tiers_met or bar.floors_met or bar.verdicts_met or bar.met)


def test_v2_floors_are_used_when_v1_lists_none(engine):
    v1 = [r for r in v1_rows(load_export(FAIL)) if r.agent == "pr_risk"]
    v1 = [compare_v1.replace(r, output={"floors": []}) if r.subject_id == 6 else r for r in v1]
    with Session(engine) as db:
        rows = v2_rows(db, OBSERVING)

    bar = compare(v1, rows, start=OBSERVING, observing=OBSERVING).bar

    assert len(bar.floored_lower) == 1


def test_a_lower_tier_on_a_pr_neither_side_floored_passes_that_rule(engine):
    # PR 10: v1 T2, v2 T1, no floors on either side.
    assert _report(engine).bar.floors_met


@pytest.fixture
def offline(monkeypatch):
    calls = []

    def refuse(*args, **kwargs):
        calls.append(args)
        raise AssertionError("no network")

    monkeypatch.setattr(GitHubClient, "_send", refuse)
    monkeypatch.setattr(httpx.Client, "send", refuse)
    return calls


def test_the_report_shows_agreement_and_the_bar_met(engine, offline, capsys):
    before = _count(engine)

    assert main([str(PASS)], engine=engine) == 0

    out = capsys.readouterr().out
    assert "Period: 2026-10-05 09:30 UTC to 2026-10-07 10:00 UTC" in out
    assert "- Commits compared: 10" in out
    assert "- Agreement on tier: 9 of 10 (90%)" in out
    assert "- Agreement on points: 1 of 2 (50%)" in out
    assert "- Not compared, v1 only: 1 commits (v2's run failed)" in out
    assert "- Not compared, v2 only: 1 commits" in out
    assert "- v1 `/tier` rulings: 1" in out
    assert "## The bar for cutover (fixed 2026-10-14): met" in out
    assert _count(engine) == before
    assert offline == []


def test_the_report_shows_the_bar_not_met(engine, capsys):
    assert main([str(FAIL)], engine=engine) == 0

    out = capsys.readouterr().out
    assert "- Agreement on tier: 7 of 10 (70%)" in out
    assert "## The bar for cutover (fixed 2026-10-14): not met" in out
    assert "on a PR v1 floored: 1, not met" in out
    assert "  - PR #6 @ 06aaaaa: v1 T3, v2 T2 (floors: pipeline)" in out
    assert "Bugs (CI results should match exactly): 1" in out


def test_markdown_writes_the_same_report(engine, tmp_path, capsys):
    out_file = tmp_path / "report.md"

    assert main([str(PASS), "--markdown", str(out_file)], engine=engine) == 0

    assert out_file.read_text() == capsys.readouterr().out


def test_since_moves_the_period(engine, capsys):
    assert main([str(PASS), "--since", "2026-10-01"], engine=engine) == 0

    out = capsys.readouterr().out
    assert "Period: 2026-10-01 00:00 UTC" in out
    assert "(v2 wasn't observing yet)" in out


def test_an_export_for_another_repository_stops_the_comparison(engine, monkeypatch, capsys):
    monkeypatch.setenv("GITHUB_REPO", "pragmattie/elsewhere")

    assert main([str(PASS)], engine=engine) == 1

    assert f"This export is for {REPO}, not pragmattie/elsewhere." in capsys.readouterr().err


def test_a_malformed_export_stops_the_comparison_with_a_readable_message(engine, tmp_path, capsys):
    broken = tmp_path / "export.json"
    broken.write_text("{not json")

    assert main([str(broken)], engine=engine) == 1

    assert f"The export {broken} isn't valid JSON: " in capsys.readouterr().err
