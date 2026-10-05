import json
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sdlc import scoring
from sdlc.db import Base
from sdlc.scoring import Features
from sdlc.tables import CIRun, Engineer, Incident, PullRequest

THURSDAY = datetime(2026, 10, 1)
FRIDAY = datetime(2026, 10, 2)
SATURDAY = datetime(2026, 10, 3)
REFERENCE = datetime(2026, 9, 1, 10, 0)


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _pr(db, opened, merged=None, **fields):
    pr = PullRequest(
        title="pr",
        state="merged" if merged else "open",
        created_at=opened,
        merged_at=merged,
        **fields,
    )
    db.add(pr)
    db.flush()
    return pr


def _incident(db, pr, opened):
    db.add(Incident(title="i", severity="sev2", opened_at=opened, caused_by_pr_id=pr.id))
    db.flush()


def _author(db, login="dev"):
    engineer = Engineer(login=login, name=login)
    db.add(engineer)
    db.flush()
    return engineer


def _history(db, module, merged, incidents, author_id=None, opened_at=None):
    """``merged`` PRs merged before REFERENCE, the first ``incidents`` of them causing one."""
    for index in range(merged):
        at = REFERENCE - timedelta(days=30 - index % 25, hours=index)
        pr = _pr(db, at - timedelta(days=1), at, module=module, author_id=author_id)
        if index < incidents:
            _incident(db, pr, opened_at or at + timedelta(hours=1))


@pytest.mark.parametrize(
    ("lines", "points"),
    [(0, 0), (49, 0), (50, 5), (149, 5), (150, 10), (399, 10), (400, 15), (799, 15), (800, 20)],
)
def test_change_size_bands(lines, points):
    assert scoring.change_size(lines) == points


@pytest.mark.parametrize(
    ("files", "points"),
    [(2, 0), (3, 2), (5, 2), (6, 4), (10, 4), (11, 6), (20, 6), (21, 7)],
)
def test_blast_radius_file_bands(files, points):
    assert scoring.blast_radius(files, 1) == points


def test_blast_radius_adds_three_for_more_than_two_modules_capped_at_ten():
    assert scoring.blast_radius(2, 2) == 0
    assert scoring.blast_radius(2, 3) == 3
    assert scoring.blast_radius(20, 3) == 9
    assert scoring.blast_radius(21, 3) == 10


def test_module_risk_scales_to_the_highest_rate():
    assert scoring.module_risk(0.1, 0.2) == 10
    assert scoring.module_risk(0.2, 0.2) == 20
    assert scoring.module_risk(0.0, 0.2) == 0
    assert scoring.module_risk(0.0, 0.0) == 0


def test_schema_migration():
    assert scoring.schema_migration(True) == 15
    assert scoring.schema_migration(False) == 0


@pytest.mark.parametrize(
    ("ratio", "points"),
    [(None, 5), (0.0, 0), (0.5, 2), (1.0, 5), (1.5, 8), (2.0, 10), (3.0, 10)],
)
def test_author_record(ratio, points):
    assert scoring.author_record(ratio) == points


def test_tests_with_change():
    assert scoring.tests_with_change(False, 50, 0) == 10
    assert scoring.tests_with_change(False, 49, 0) == 0
    assert scoring.tests_with_change(True, 500, 0) == 0
    assert scoring.tests_with_change(False, 500, 1) == 0


@pytest.mark.parametrize(("failures", "points"), [(0, 0), (1, 4), (2, 7), (3, 10), (9, 10)])
def test_ci_signal(failures, points):
    assert scoring.ci_signal(failures) == points


def test_review_depth():
    assert scoring.review_depth(0, 10) == 5
    assert scoring.review_depth(1, 399) == 0
    assert scoring.review_depth(1, 400) == 5
    assert scoring.review_depth(2, 400) == 0


@pytest.mark.parametrize(
    ("at", "points"),
    [
        (FRIDAY.replace(hour=9), 5),
        (THURSDAY.replace(hour=17, minute=59), 0),
        (THURSDAY.replace(hour=18), 5),
        (THURSDAY.replace(hour=7, minute=59), 5),
        (THURSDAY.replace(hour=8), 0),
        (SATURDAY.replace(hour=12), 5),
        (SATURDAY + timedelta(days=1, hours=12), 5),
    ],
)
def test_timing(at, points):
    assert scoring.timing(at) == points


@pytest.mark.parametrize(("commits", "points"), [(0, 0), (1, 2), (2, 3), (3, 5), (8, 5)])
def test_rework_churn(commits, points):
    assert scoring.rework_churn(commits) == points


QUIET = Features(
    lines=10,
    files_changed=1,
    modules_touched=1,
    touches_migration=False,
    docs_only=False,
    test_files_changed=1,
    review_count=2,
    rework_commits=0,
    real_ci_failures=0,
    at=THURSDAY.replace(hour=10),
    module_rate=0.0,
    max_module_rate=0.0,
    author_ratio=0.0,
)


def test_score_features_keeps_each_signals_points():
    score = scoring.score_features(QUIET)
    assert set(score.signals) == set(scoring.MAX_POINTS)
    assert score.total == 0
    score = scoring.score_features(replace(QUIET, lines=200, touches_migration=True))
    assert score.signals["change_size"] == 10
    assert score.signals["schema_migration"] == 15
    assert score.total == 25


def test_score_features_caps_the_total_at_100():
    assert sum(scoring.MAX_POINTS.values()) > 100
    worst = Features(
        lines=1000,
        files_changed=30,
        modules_touched=4,
        touches_migration=True,
        docs_only=False,
        test_files_changed=0,
        review_count=0,
        rework_commits=5,
        real_ci_failures=5,
        at=SATURDAY,
        module_rate=0.5,
        max_module_rate=0.5,
        author_ratio=4.0,
    )
    score = scoring.score_features(worst)
    assert score.signals == scoring.MAX_POINTS
    assert score.total == 100


def test_module_rates_shrink_toward_the_team_rate(session):
    _history(session, "billing_auth", merged=10, incidents=5)
    _history(session, "leads", merged=30, incidents=0)
    rates, team = scoring.module_rates(session, REFERENCE)
    assert team == pytest.approx(5 / 40)
    assert rates["billing_auth"] == pytest.approx((5 + team * 10) / 20)
    assert rates["leads"] == pytest.approx((0 + team * 10) / 40)


def test_module_rates_ignore_later_incidents_and_later_merges(session):
    _history(session, "billing_auth", merged=10, incidents=5, opened_at=REFERENCE + timedelta(1))
    _pr(session, REFERENCE - timedelta(days=2), REFERENCE + timedelta(hours=1), module="leads")
    rates, team = scoring.module_rates(session, REFERENCE)
    assert team == 0
    assert rates == {"billing_auth": 0.0}


def test_module_rates_with_no_history(session):
    assert scoring.module_rates(session, REFERENCE) == ({}, 0.0)


def test_author_ratio_is_their_incident_share_over_the_team_rate(session):
    author = _author(session)
    _history(session, "leads", merged=10, incidents=5, author_id=author.id)
    _history(session, "accounts", merged=10, incidents=0)
    _, team = scoring.module_rates(session, REFERENCE)
    assert team == pytest.approx(0.25)
    assert scoring.author_ratio(session, author.id, REFERENCE, team) == pytest.approx(2.0)


def test_author_ratio_uses_only_the_last_twenty_merged_prs(session):
    author = _author(session)
    old = REFERENCE - timedelta(days=200)
    for _ in range(5):
        _incident(session, _pr(session, old, old, author_id=author.id), old)
    _history(session, "leads", merged=20, incidents=2, author_id=author.id)
    assert scoring.author_ratio(session, author.id, REFERENCE, 0.2) == pytest.approx(0.5)


def test_author_ratio_unknown_cases(session):
    author = _author(session)
    _history(session, "leads", merged=4, incidents=2, author_id=author.id)
    _history(session, "accounts", merged=10, incidents=1)
    assert scoring.author_ratio(session, author.id, REFERENCE, 0.2) is None
    assert scoring.author_ratio(session, None, REFERENCE, 0.2) is None
    _history(session, "leads", merged=1, incidents=0, author_id=author.id)
    assert scoring.author_ratio(session, author.id, REFERENCE, 0.2) is not None
    assert scoring.author_ratio(session, author.id, REFERENCE, 0.0) is None


def test_author_ratio_ignores_incidents_opened_after_the_reference(session):
    author = _author(session)
    _history(
        session,
        "leads",
        merged=10,
        incidents=5,
        author_id=author.id,
        opened_at=REFERENCE + timedelta(hours=1),
    )
    _history(session, "accounts", merged=10, incidents=2)
    assert scoring.author_ratio(session, author.id, REFERENCE, 0.1) == 0.0


def test_compute_features_does_not_peek_past_the_pr_opening(session):
    author = _author(session)
    _history(session, "billing_auth", merged=10, incidents=0, author_id=author.id)
    _history(session, "leads", merged=10, incidents=1)
    pr = _pr(session, REFERENCE, module="billing_auth", author_id=author.id)
    before = scoring.compute_features(session, pr, now=THURSDAY)
    for earlier in session.query(PullRequest).filter(PullRequest.module == "billing_auth"):
        _incident(session, earlier, REFERENCE + timedelta(hours=2))
    after = scoring.compute_features(session, pr, now=THURSDAY)
    assert after == before
    assert before.author_ratio == 0.0


def test_compute_features_reads_the_pr(session):
    _history(session, "billing_auth", merged=10, incidents=4)
    _history(session, "leads", merged=10, incidents=0)
    pr = _pr(
        session,
        REFERENCE,
        module="leads",
        additions=120,
        deletions=30,
        files_changed=4,
        modules_touched=3,
        touches_migration=True,
        test_files_changed=0,
        review_count=1,
        rework_commits=2,
    )
    for conclusion, flaky in [("failure", False), ("failure", True), ("success", False)]:
        session.add(
            CIRun(
                pull_request_id=pr.id,
                suite="api",
                conclusion=conclusion,
                flaky=flaky,
                started_at=REFERENCE,
            )
        )
    session.flush()
    features = scoring.compute_features(session, pr, now=THURSDAY)
    rates, _ = scoring.module_rates(session, REFERENCE)
    assert features.lines == 150
    assert features.files_changed == 4
    assert features.modules_touched == 3
    assert features.touches_migration is True
    assert features.real_ci_failures == 1
    assert features.module_rate == pytest.approx(rates["leads"])
    assert features.max_module_rate == pytest.approx(rates["billing_auth"])
    assert features.author_ratio is None
    score = scoring.score_pull_request(session, pr, now=THURSDAY)
    assert score == scoring.score_features(features)


def test_compute_features_module_rate_is_zero_without_a_module(session):
    _history(session, "billing_auth", merged=10, incidents=4)
    pr = _pr(session, REFERENCE)
    assert scoring.compute_features(session, pr).module_rate == 0.0


def test_at_falls_back_from_merged_to_now_to_created(session):
    merged = THURSDAY.replace(hour=10)
    assert scoring.compute_features(session, _pr(session, REFERENCE, merged), FRIDAY).at == merged
    assert scoring.compute_features(session, _pr(session, REFERENCE), FRIDAY).at == FRIDAY
    assert scoring.compute_features(session, _pr(session, REFERENCE)).at == REFERENCE


def test_features_digest_is_json_safe():
    digest = scoring.features_digest(QUIET)
    assert digest["at"] == "2026-10-01T10:00:00"
    assert digest["author_ratio"] == 0.0
    assert json.loads(json.dumps(digest)) == digest
