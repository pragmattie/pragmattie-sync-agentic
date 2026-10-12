from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

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
from sdlc.suite_selector import recommend
from sdlc.tables import AgentDecision, CIRun, PullRequest
from sdlc.tiers import load_policy

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
