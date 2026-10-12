from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import suite_selector
from sdlc.agents.suite_select import (
    CI_SUITES,
    TESTS_END,
    TESTS_START,
    Flaky,
    Selection,
    by_paths,
    flaky_suites,
    replace_section,
    section_lines,
    select_suites,
    with_flaky,
)
from sdlc.db import Base
from sdlc.suite_selector import ci_results, describe, recommend, report, settle, unsettled
from sdlc.tables import AgentDecision, CIRun, PullRequest
from sdlc.tiers import load_policy
from tests.fakes import FakeGitHub, iso

NOW = datetime(2026, 10, 6, 12, 0)
SHA = "a" * 40


@pytest.fixture(scope="module")
def policy():
    return load_policy()


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _select(paths, policy, tier="T1", assessed=True):
    return select_suites(list(paths), tier=tier, policy=policy, assessed=assessed)


def test_crm_web_selects_only_crm_web(policy):
    selection = _select(["apps/crm-web/src/App.vue"], policy)
    assert selection.suites == ("web",)
    assert selection.reasons == {"web": "code changed in apps/crm-web/src/App.vue"}
    assert selection.skipped == ("api", "orchestrator", "migrations", "insights-web")
    assert selection.full is None


def test_insights_web_selects_only_insights_web(policy):
    assert _select(["apps/insights-web/src/main.js"], policy).suites == ("insights-web",)


def test_api_source_selects_api_and_migrations_and_its_tests_only_api(policy):
    source = _select(["apps/api/app/main.py"], policy)
    assert source.suites == ("api", "migrations")
    assert source.reasons["migrations"] == "code changed in apps/api/app/main.py"
    tests = _select(["apps/api/tests/test_leads.py"], policy)
    assert tests.suites == ("api",)
    assert tests.reasons == {"api": "tests changed in apps/api/tests/test_leads.py"}


def test_orchestrator_migrations_select_orchestrator_and_migrations(policy):
    path = "orchestrator/migrations/versions/0009_more.py"
    selection = _select([path], policy)
    assert selection.suites == ("orchestrator", "migrations")
    assert set(selection.reasons.values()) == {f"code changed in {path}"}


def test_docs_only_selects_nothing_and_says_so(policy):
    selection = _select(["README.md", "docs/adr/x.md"], policy)
    assert (selection.suites, selection.full) == ((), None)
    assert selection.skipped == CI_SUITES
    assert "- Would run no jobs: only docs or config changed." in section_lines(selection)


@pytest.mark.parametrize(
    ("path", "full"),
    [
        (".github/workflows/ci.yml", "it changes the CI workflow (.github/workflows/ci.yml)"),
        ("scripts/x.sh", "no rule covers scripts/x.sh"),
    ],
)
def test_a_workflow_or_unknown_path_selects_every_job(policy, path, full):
    selection = _select(["apps/crm-web/src/App.vue", path], policy)
    assert selection.suites == CI_SUITES
    assert selection.full == full
    assert set(selection.reasons.values()) == {"every job runs"}
    assert f"- Would run every job: {full}." in section_lines(selection)


def test_t2_selects_every_job(policy):
    selection = _select(["apps/crm-web/src/App.vue"], policy, tier="T2")
    assert selection.suites == CI_SUITES
    assert selection.full == "T2 runs the full suite (full suite)"


def test_a_failed_assessment_selects_every_job(policy):
    selection = _select(["README.md"], policy, tier="T0", assessed=False)
    assert selection.suites == CI_SUITES
    assert selection.full == "the risk agent couldn't score this commit, so nothing is skipped"


def test_the_first_reason_per_suite_wins(policy):
    reasons, full = by_paths(["apps/api/tests/test_a.py", "apps/api/app/b.py", "apps/api/app/c.py"])
    assert full is None
    assert reasons == {
        "api": "tests changed in apps/api/tests/test_a.py",
        "migrations": "code changed in apps/api/app/b.py",
    }


def test_no_paths_select_nothing():
    assert by_paths([]) == ({}, None)


def _runs(db, suite, runs, flaky, *, source="synthetic", days_ago=1):
    for index in range(runs):
        db.add(
            CIRun(
                source=source,
                suite=suite,
                conclusion="success",
                flaky=index < flaky,
                started_at=NOW - timedelta(days=days_ago, minutes=index),
            )
        )
    db.flush()


def test_four_flaky_of_forty_is_flagged_with_its_simulated_count(db):
    _runs(db, "api", 30, 4)
    _runs(db, "api", 10, 0, source="github")
    assert flaky_suites(db, NOW) == [Flaky("api", 0.1, 40, 30)]


def test_too_few_none_flaky_old_or_unknown_suites_are_not_flagged(db):
    _runs(db, "api", 40, 0)
    _runs(db, "web", 10, 5)
    _runs(db, "orchestrator", 40, 10, days_ago=61)
    _runs(db, "lint", 40, 10)
    assert flaky_suites(db, NOW) == []


def test_only_the_selected_jobs_keep_their_flags(policy):
    flags = [Flaky("api", 0.1, 40, 0), Flaky("web", 0.05, 20, 0)]
    selection = with_flaky(_select(["apps/crm-web/src/App.vue"], policy), flags)
    assert selection.flaky == (Flaky("web", 0.05, 20, 0),)


def test_the_flaky_wording(policy):
    selection = with_flaky(
        _select(["apps/crm-web/a.vue", "apps/insights-web/b.vue"], policy),
        [Flaky("web", 0.1, 40, 30), Flaky("insights-web", 0.05, 20, 0)],
    )
    lines = section_lines(selection)
    assert lines[-3:-1] == [
        "- Possibly flaky: CRM web failed and then passed on a re-run in 10% of 40 recent runs, "
        "30 of them simulated. Re-run it before assuming a real failure.",
        "- Possibly flaky: Insights web failed and then passed on a re-run in 5% of 20 recent "
        "runs. Re-run it before assuming a real failure.",
    ]


def test_the_section_lists_what_runs_and_what_is_skipped(policy):
    selection = _select(["apps/api/app/main.py"], policy)
    assert section_lines(selection) == [
        TESTS_START,
        "**Tests** (the test selector's recommendation; CI still runs every job)",
        "- Would run: **CRM API, Migrations**",
        "  - CRM API: code changed in apps/api/app/main.py",
        "  - Migrations: code changed in apps/api/app/main.py",
        "- Would skip: Orchestrator, CRM web, Insights web",
        TESTS_END,
    ]
    every = _select(["apps/api/app/main.py"], policy, tier="T3")
    assert "- Would skip" not in "\n".join(section_lines(every))


def test_replace_section_swaps_only_its_own_block(policy):
    old = "\n".join(section_lines(_select(["apps/crm-web/a.vue"], policy)))
    other = "<!-- overrides -->\nkept\n<!-- /overrides -->"
    body = f"head\n\n{old}\n\n{other}\n"
    new = _select(["apps/crm-web/a.vue"], policy, tier="T3")
    replaced = replace_section(body, new)
    assert replaced == f"head\n\n{chr(10).join(section_lines(new))}\n\n{other}\n"


def test_replace_section_leaves_a_body_without_one_unchanged(policy):
    body = "## Risk review: T1 (Light)\n\nno tests section\n"
    assert replace_section(body, _select(["README.md"], policy)) == body


def test_a_selection_reads_back_from_its_record(policy):
    selection = with_flaky(_select(["apps/crm-web/a.vue"], policy), [Flaky("web", 0.1, 40, 3)])
    assert Selection.from_record(selection.as_record()) == selection


def test_recommend_records_one_row_per_attempt(db, policy):
    pr = PullRequest(
        source="github", external_id="pr-7", number=7, title="x", created_at=NOW - timedelta(1)
    )
    db.add(pr)
    _runs(db, "web", 20, 2)
    paths = ["apps/crm-web/a.vue"] + [f"docs/{n}.md" for n in range(400)]
    selection = recommend(
        db, pr, SHA, paths, tier="T1", policy=policy, assessed=True, now=NOW, trigger="poll"
    )
    assert selection.flaky == (Flaky("web", 0.1, 20, 20),)
    recommend(
        db, pr, SHA, paths, tier="T3", policy=policy, assessed=True, now=NOW, trigger="tier_change"
    )
    first, second = db.scalars(select(AgentDecision).order_by(AgentDecision.id))
    assert (first.agent, first.agent_version, first.subject_type) == ("test_selector", "v1", "pr")
    assert (first.subject_source, first.subject_id, first.head_sha) == ("github", 7, SHA)
    assert (first.attempt, first.tier, first.trigger, first.status) == (1, "T1", "poll", "ok")
    assert first.output == {**selection.as_record(), "paths": paths[:300]}
    assert first.action_taken == {"shown_in": "the risk comment's Tests section"}
    assert (second.attempt, second.tier, second.trigger) == (2, "T3", "tier_change")
    assert second.output["suites"] == list(CI_SUITES)


JOB_NAMES = {
    "api": "CRM API (lint + tests)",
    "web": "CRM web (tests + build)",
    "insights-web": "Insights web (tests + build)",
    "orchestrator": "Orchestrator (lint + tests)",
    "migrations": "Migrations on MySQL",
}


def _job(suite, conclusion="success", *, attempt=1, seconds=60, status="completed"):
    start = NOW - timedelta(hours=1)
    return {
        "name": JOB_NAMES.get(suite, suite),
        "status": status,
        "conclusion": conclusion if status == "completed" else None,
        "run_attempt": attempt,
        "started_at": iso(start),
        "completed_at": iso(start + timedelta(seconds=seconds)) if status == "completed" else None,
    }


def _ci(gh, sha=SHA, **overrides):
    """One CI run on the commit with every job passing, 60 seconds each, but for ``overrides``."""
    jobs = []
    for suite in CI_SUITES:
        jobs += overrides.get(suite, [_job(suite)])
    return gh.add_run(sha, jobs)


def _pr(db, number=7):
    pr = PullRequest(
        source="github",
        external_id=f"pr-{number}",
        number=number,
        title="x",
        created_at=NOW - timedelta(1),
    )
    db.add(pr)
    return pr


def _recommend(db, policy, pr, sha=SHA, paths=("apps/crm-web/a.vue",), *, now=NOW, tier="T1"):
    return recommend(db, pr, sha, list(paths), tier=tier, policy=policy, assessed=True, now=now)


def _results(db):
    query = select(AgentDecision).where(AgentDecision.trigger == "ci_result")
    return list(db.scalars(query.order_by(AgentDecision.id)))


def test_ci_results_waits_for_every_job_and_then_reads_each_final_attempt():
    gh = FakeGitHub()
    assert ci_results(gh, SHA) is None
    _ci(gh, web=[_job("web", status="in_progress")])
    assert ci_results(gh, SHA) is None
    gh.jobs[gh.runs[0]["id"]][CI_SUITES.index("web")] = _job("web", seconds=95)
    results = ci_results(gh, SHA)
    assert set(results) == set(CI_SUITES)
    assert results["web"] == {"conclusion": "success", "flaky": False, "seconds": 95}
    assert ("GET", "/actions/runs") in gh.requests


def test_ci_results_waits_while_a_job_is_missing_or_cancelled():
    gh = FakeGitHub()
    gh.add_run(SHA, [_job(suite) for suite in CI_SUITES if suite != "api"])
    assert ci_results(gh, SHA) is None
    gh.runs.clear()
    _ci(gh, api=[_job("api", "cancelled")])
    assert ci_results(gh, SHA) is None
    _ci(gh, api=[_job("api", "skipped")])
    assert ci_results(gh, SHA) is None


def test_ci_results_takes_the_last_attempt_across_runs_and_flags_a_failed_then_passed_job():
    gh = FakeGitHub()
    gh.add_run(
        SHA,
        [_job(s) for s in CI_SUITES if s != "api"]
        + [_job("api", attempt=2), _job("api", "failure", attempt=1), _job("Deploy", "failure")],
    )
    results = ci_results(gh, SHA)
    assert results["api"]["flaky"] is True
    assert results["api"]["conclusion"] == "success"
    _ci(gh, migrations=[_job("migrations", "failure")])  # a newer run: its result wins
    gh.runs[-1]["created_at"] = iso(NOW)
    results = ci_results(gh, SHA)
    assert results["migrations"] == {"conclusion": "failure", "flaky": False, "seconds": 60}
    assert results["api"]["flaky"] is True  # an earlier run's failure still counts


def test_a_job_without_times_counts_no_seconds():
    gh = FakeGitHub()
    _ci(gh, web=[{**_job("web"), "started_at": None}])
    assert ci_results(gh, SHA)["web"]["seconds"] == 0


def test_settle_records_one_ok_row_once_every_job_finishes_and_then_nothing(db, policy):
    gh = FakeGitHub()
    pr = _pr(db)
    recommendation = _recommend(db, policy, pr)
    rows = list(db.scalars(select(AgentDecision)))
    later = NOW + timedelta(minutes=30)
    _ci(gh, api=[_job("api", status="queued")])
    assert settle(db, gh, later) == 0
    gh.jobs[gh.runs[0]["id"]][0] = _job("api", seconds=120)
    assert settle(db, gh, later) == 1
    [row] = _results(db)
    assert (row.agent, row.trigger, row.status) == ("test_selector", "ci_result", "ok")
    assert row.tier == "T1"
    assert (row.subject_id, row.head_sha, row.attempt) == (7, SHA, 2)
    assert row.created_at == later
    assert row.output["recommendation_id"] == rows[0].id
    assert row.output["suites"] == list(recommendation.suites) == ["web"]
    assert row.output["skipped"] == ["api", "orchestrator", "migrations", "insights-web"]
    assert (row.output["missed"], row.output["caught"]) == ([], [])
    assert row.output["saved_seconds"] == 120 + 60 * 3
    assert row.output["results"]["api"]["seconds"] == 120
    assert settle(db, gh, later + timedelta(minutes=2)) == 0
    assert [path for method, path in gh.requests if method != "GET"] == []


def test_a_failed_skipped_job_is_a_miss_and_a_failed_selected_job_is_caught(db, policy):
    gh = FakeGitHub()
    _recommend(db, policy, _pr(db))
    _ci(
        gh,
        web=[_job("web", "failure")],
        api=[_job("api", "failure")],
        orchestrator=[_job("orchestrator", "failure"), _job("orchestrator", attempt=2)],
    )
    settle(db, gh, NOW)
    [row] = _results(db)
    assert row.status == "missed"
    assert (row.output["missed"], row.output["caught"]) == (["api"], ["web"])
    assert row.output["results"]["orchestrator"]["flaky"] is True


def test_the_newest_recommendation_is_graded_and_old_commits_are_not_read(db, policy):
    gh = FakeGitHub()
    pr = _pr(db)
    _recommend(db, policy, pr, now=NOW - timedelta(days=8), sha="b" * 40)
    _recommend(db, policy, pr, now=NOW - timedelta(hours=2))
    newest = _recommend(db, policy, pr, now=NOW - timedelta(hours=1), tier="T3")
    assert [(row.head_sha, row.tier) for row in unsettled(db, NOW)] == [(SHA, "T3")]
    _ci(gh)
    _ci(gh, sha="b" * 40)
    assert settle(db, gh, NOW) == 1
    [row] = _results(db)
    assert (row.tier, row.attempt, row.output["suites"]) == ("T3", 3, list(newest.suites))
    assert row.output["saved_seconds"] == 0
    assert unsettled(db, NOW) == []
    runs_read = [path for _, path in gh.requests if path == "/actions/runs"]
    assert len(runs_read) == 1


def _settled(db, number, sha, *, missed=(), caught=(), flaky=(), saved=0, suites=("web",)):
    skipped = [suite for suite in CI_SUITES if suite not in suites]
    results = {
        suite: {
            "conclusion": "failure" if suite in missed or suite in caught else "success",
            "flaky": suite in flaky,
            "seconds": 60,
        }
        for suite in CI_SUITES
    }
    db.add(
        AgentDecision(
            created_at=NOW,
            agent="test_selector",
            agent_version="v1",
            subject_type="pr",
            subject_source="github",
            subject_id=number,
            trigger="ci_result",
            head_sha=sha,
            attempt=2,
            status="missed" if missed else "ok",
            output={
                "recommendation_id": 1,
                "suites": list(suites),
                "skipped": skipped,
                "results": results,
                "missed": list(missed),
                "caught": list(caught),
                "saved_seconds": saved,
            },
        )
    )
    db.flush()


def test_nothing_settled_says_so(db):
    assert report(db)["commits"] == 0
    assert describe(report(db)) == (
        "No commits have finished CI since the test selector started recommending."
    )


def test_report_counts_every_settled_commit(db):
    _settled(db, 7, "a" * 40, missed=("api",), caught=("web",), saved=240)
    _settled(db, 8, "b" * 40, flaky=("web", "orchestrator"), saved=130, suites=CI_SUITES)
    assert report(db) == {
        "commits": 2,
        "run": 6,
        "skipped": 4,
        "misses": [(7, "api")],
        "caught": 1,
        "flaky": 2,
        "saved_minutes": 6.2,
    }
    assert describe(report(db)) == (
        "Test selector track record: 2 commits with CI results.\n"
        "  Would have run 6 jobs and skipped 4, saving about 6.2 CI minutes.\n"
        "  Skipped jobs that really failed (misses): 1\n"
        "    PR #7: api\n"
        "  Failures caught by jobs it would have run: 1\n"
        "  Jobs that failed and then passed on a re-run (flaky): 2\n"
        "  CI still runs every job, so these are what skipping would have done, not what it did."
    )


def test_one_commit_is_singular(db):
    _settled(db, 7, SHA, saved=240)
    text = describe(report(db))
    assert text.startswith("Test selector track record: 1 commit with CI results.\n")
    assert "  Skipped jobs that really failed (misses): 0\n  Failures caught" in text


def test_the_report_command_prints_the_track_record(sqlite_engine, capsys):
    Base.metadata.create_all(sqlite_engine)
    assert suite_selector.main(["report"], engine=sqlite_engine) == 0
    assert capsys.readouterr().out == (
        "No commits have finished CI since the test selector started recommending.\n"
    )
