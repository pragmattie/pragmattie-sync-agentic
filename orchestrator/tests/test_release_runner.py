import json
import logging
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import release_runner
from sdlc.audit import record_decision
from sdlc.db import Base
from sdlc.github_client import GitHubError
from sdlc.governance import assign_tier, facts_of
from sdlc.release_runner import DEPLOY_WORKFLOW, ReleaseRunner
from sdlc.scoring import score_pull_request
from sdlc.signals.github import SUITES, Collector
from sdlc.tables import AgentDecision, GateStatus
from sdlc.tiers import load_policy
from tests.fakes import FakeGitHub

NOON = datetime(2026, 10, 6, 12, 0)
PIPELINE = ("apps/api/app/routers/pipeline.py",)  # floored at T2
MIGRATION = ("orchestrator/migrations/versions/0009_more.py",)  # floored at T3
MERGE = "m" * 40
HELD = "T2 · Held: open incident in pipeline (#61)"


@pytest.fixture(scope="module")
def policy():
    return load_policy()


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    return sqlite_engine


@pytest.fixture
def gh():
    return FakeGitHub()


@pytest.fixture(autouse=True)
def runner_logs(monkeypatch):
    """Alembic's fileConfig in other tests disables loggers that already exist; undo that."""
    monkeypatch.setattr(release_runner.log, "disabled", False)


def _runner(gh, engine, policy, mode="enforce"):
    return ReleaseRunner(gh, policy, mode, engine=engine)


def _merged(gh, number, sha, at, files=PIPELINE):
    gh.add_pr(number, f"{number:x}".rjust(40, "h"), files=files)
    gh.merge(number, at, sha=sha)


def _ci(gh, sha, conclusion="success", jobs=SUITES):
    gh.add_run(
        sha, [{"name": name, "status": "completed", "conclusion": conclusion} for name in jobs]
    )


def _incident(gh, number=61, module="pipeline", at=NOON - timedelta(hours=2)):
    labels = ("incident", f"module:{module}") if module else ("incident",)
    gh.add_issue(number, "Pipeline degraded", labels=labels, created_at=at)


def _release(gh, sha=MERGE, *, number=40, ci="success", files=PIPELINE):
    """A PR merged as ``sha``, its CI finished and the deploy run waiting on it."""
    _merged(gh, number, sha, NOON - timedelta(hours=1), files=files)
    if ci:
        _ci(gh, sha, ci)
    gh.add_workflow_run(DEPLOY_WORKFLOW, sha)


def _rows(engine):
    with Session(engine) as db:
        query = select(AgentDecision).where(AgentDecision.agent == "release_gate")
        return list(db.scalars(query.order_by(AgentDecision.id)))


def _gates(gh, sha=MERGE):
    return [s for s in gh.statuses_for(sha) if s["context"] == "release-gate"]


def test_an_open_incident_holds_the_release_until_it_closes(gh, engine, policy):
    _release(gh)
    _incident(gh)
    runner = _runner(gh, engine, policy)

    summary = runner.poll_once(NOON)

    assert summary == {"mode": "enforce", "releases": 1, "assessed": 1, "errors": 0}
    [status] = _gates(gh)
    assert (status["state"], status["description"]) == ("pending", HELD)
    [hold] = _rows(engine)
    assert (hold.subject_type, hold.subject_id, hold.head_sha) == ("release", 40, MERGE)
    assert (hold.attempt, hold.trigger, hold.tier) == (1, "poll", "T2")
    assert hold.output["verdict"] == "hold" and hold.output["mode"] == "enforce"
    assert hold.output["prs"] == [40] and hold.output["modules"] == ["pipeline"]
    assert hold.output["open_incidents"] == [
        {"module": "pipeline", "number": 61, "title": "Pipeline degraded"}
    ]
    assert hold.action_taken == {
        "state": "pending",
        "description": HELD,
        "done": "release-gate pending",
    }

    writes = len(gh.writes())
    assert runner.poll_once(NOON + timedelta(minutes=1))["assessed"] == 0
    assert len(gh.writes()) == writes
    assert len(_rows(engine)) == 1

    gh.close_issue(61, NOON + timedelta(minutes=2))
    runner.poll_once(NOON + timedelta(minutes=3))

    assert [(s["state"], s["description"]) for s in _gates(gh)][-1] == (
        "success",
        "T2 · Release: checks passed",
    )
    hold, release = _rows(engine)
    assert (release.output["verdict"], release.attempt) == ("release", 2)
    assert (release.subject_id, release.head_sha) == (40, MERGE)


def test_an_incident_elsewhere_or_without_a_module_never_holds(gh, engine, policy):
    _release(gh)
    _incident(gh, 61, module="billing_auth")
    _incident(gh, 62, module=None)

    _runner(gh, engine, policy).poll_once(NOON)

    assert _gates(gh)[-1]["description"] == "T2 · Release: checks passed"


def test_a_release_waits_for_ci_then_releases(gh, engine, policy):
    _release(gh, ci=None)
    _ci(gh, MERGE, jobs=list(SUITES)[:4])  # one job still to report
    runner = _runner(gh, engine, policy)

    runner.poll_once(NOON)
    assert _gates(gh)[-1]["description"] == "T2 · Held: waiting for CI on the release commit"
    assert _gates(gh)[-1]["state"] == "pending"

    _ci(gh, MERGE, jobs=list(SUITES)[4:])
    runner.poll_once(NOON + timedelta(minutes=1))
    assert (_gates(gh)[-1]["state"], _gates(gh)[-1]["description"]) == (
        "success",
        "T2 · Release: checks passed",
    )
    assert [row.output["ci"] for row in _rows(engine)] == ["running", "passed"]


def test_failed_ci_blocks_the_release(gh, engine, policy):
    _release(gh, ci="failure")

    _runner(gh, engine, policy).poll_once(NOON)

    assert (_gates(gh)[-1]["state"], _gates(gh)[-1]["description"]) == (
        "failure",
        "T2 · Blocked: CI failed on the release commit",
    )
    assert _rows(engine)[0].output["verdict"] == "blocked"


def test_t0_and_t1_release_at_once_and_t3_asks_for_the_deployment_approval(gh, engine, policy):
    _release(gh, "1" * 40, number=1, ci=None, files=("README.md",))
    _release(gh, "3" * 40, number=3, files=MIGRATION)

    _runner(gh, engine, policy).poll_once(NOON)

    assert _gates(gh, "1" * 40)[-1]["description"] == "T0 · Release: deploys automatically"
    t3 = _gates(gh, "3" * 40)[-1]
    assert (t3["state"], t3["description"]) == (
        "success",
        "T3 · Release: checks passed; approve the deployment in GitHub",
    )


def test_shadow_posts_success_with_what_enforce_would_do(gh, engine, policy):
    _release(gh)
    _incident(gh)

    summary = _runner(gh, engine, policy, "shadow").poll_once(NOON)

    assert summary["mode"] == "shadow"
    [status] = _gates(gh)
    assert status["state"] == "success"
    assert status["description"] == (
        "T2 · Shadow (would be pending). Held: open incident in pipeline (#61)"
    )
    [row] = _rows(engine)
    assert row.output["mode"] == "shadow" and row.output["would_be"] == "pending"


def test_observe_records_the_verdict_and_posts_nothing(gh, engine, policy):
    _release(gh)
    _incident(gh)
    runner = _runner(gh, engine, policy, "observe")

    summary = runner.poll_once(NOON)
    runner.poll_once(NOON + timedelta(minutes=1))

    assert summary["mode"] == "observe"
    assert gh.writes() == [] and _gates(gh) == []
    [row] = _rows(engine)  # an unchanged verdict isn't recorded again
    assert row.action_taken == {
        "state": "success",
        "description": "T2 · Observe (would be pending). Held: open incident in pipeline (#61)",
        "skipped": "mode is observe",
    }
    assert row.output["mode"] == "observe" and row.output["would_be"] == "pending"


def test_observe_notes_the_skipped_status_once_a_commit(gh, engine, policy, caplog):
    _release(gh)
    _incident(gh)
    runner = _runner(gh, engine, policy, "observe")

    with caplog.at_level(logging.INFO, logger="sdlc.release_runner"):
        runner.poll_once(NOON)
        runner.poll_once(NOON + timedelta(minutes=1))
        gh.close_issue(61, NOON + timedelta(minutes=2))  # the verdict changes: a new row
        runner.poll_once(NOON + timedelta(minutes=3))

    notes = [r.getMessage() for r in caplog.records if "is not posted" in r.getMessage()]
    assert notes == [
        "Release mmmmmmm: observe mode, so release-gate "
        "(T2 · Observe (would be pending). Held: open incident in pipeline (#61)) is not posted"
    ]
    assert len(_rows(engine)) == 2
    assert gh.writes() == []


def test_off_does_nothing_at_all(gh, engine, policy):
    _release(gh)

    assert _runner(gh, engine, policy, "off").poll_once(NOON) == {"mode": "off"}
    assert gh.requests == [] and _rows(engine) == []


@pytest.mark.parametrize("status", ["completed", "waiting"])
def test_a_run_past_the_gate_is_never_judged(gh, engine, policy, status):
    _merged(gh, 40, MERGE, NOON - timedelta(hours=1))
    _ci(gh, MERGE)
    gh.add_workflow_run(DEPLOY_WORKFLOW, MERGE, status)

    summary = _runner(gh, engine, policy).poll_once(NOON)

    assert summary["releases"] == 0
    assert gh.statuses == [] and _rows(engine) == []


def test_no_deploy_workflow_means_nothing_to_judge(gh, engine, policy):
    _merged(gh, 40, MERGE, NOON - timedelta(hours=1))

    summary = _runner(gh, engine, policy).poll_once(NOON)

    assert summary == {"mode": "enforce", "releases": 0, "assessed": 0, "errors": 0}
    assert ("GET", f"/actions/workflows/{DEPLOY_WORKFLOW}/runs") in gh.requests
    assert gh.writes() == []


def test_waiting_commits_reads_a_404_by_its_status_not_its_message(gh, engine, policy):
    def failing(status):
        def get(path, **params):
            raise GitHubError("the workflow's runs couldn't be read", status=status)

        return get

    runner = _runner(gh, engine, policy)
    gh.get = failing(404)
    assert runner.waiting_commits() == []
    gh.get = failing(500)
    with pytest.raises(GitHubError):
        runner.waiting_commits()


def test_waiting_commits_are_distinct_and_newest_first(gh, engine, policy):
    gh.add_workflow_run(DEPLOY_WORKFLOW, "a" * 40, "in_progress")
    gh.add_workflow_run(DEPLOY_WORKFLOW, "b" * 40, "completed")
    gh.add_workflow_run(DEPLOY_WORKFLOW, "c" * 40, "queued")
    gh.add_workflow_run(DEPLOY_WORKFLOW, "a" * 40, "pending")

    assert _runner(gh, engine, policy).waiting_commits() == ["a" * 40, "c" * 40]


def test_a_release_starts_after_the_last_deploy(gh, engine, policy):
    first, second, third = "1" * 40, "2" * 40, "3" * 40
    _merged(gh, 40, first, NOON - timedelta(hours=4))
    _merged(gh, 41, second, NOON - timedelta(hours=3))
    _merged(gh, 42, third, NOON - timedelta(hours=2))
    # the older commit deployed after the newer merges: it ships only what was merged up to it
    deployment = gh.add_deployment(first)
    gh.deployment_status(deployment["id"], "success", NOON - timedelta(hours=1))
    _ci(gh, third)
    gh.add_workflow_run(DEPLOY_WORKFLOW, third)

    _runner(gh, engine, policy).poll_once(NOON)

    [row] = _rows(engine)
    assert (row.subject_id, row.head_sha) == (42, third)
    assert row.output["prs"] == [41, 42]
    assert (row.output["recent_deploys"], row.output["failed_deploys"]) == (1, 0)


def test_with_no_deploy_yet_the_release_is_the_head_pr(gh, engine, policy):
    _merged(gh, 39, "9" * 40, NOON - timedelta(hours=3))
    _release(gh)

    _runner(gh, engine, policy).poll_once(NOON)

    assert _rows(engine)[0].output["prs"] == [40]


def test_a_deploy_an_incident_links_to_counts_as_failed(gh, engine, policy):
    old = "0" * 40
    _merged(gh, 39, old, NOON - timedelta(days=1, hours=1))
    deployment = gh.add_deployment(old)
    gh.deployment_status(deployment["id"], "success", NOON - timedelta(days=1))
    _incident(gh, 61, module="billing_auth", at=NOON - timedelta(hours=20))
    _release(gh)

    _runner(gh, engine, policy).poll_once(NOON)

    row = _rows(engine)[0]
    assert (row.output["recent_deploys"], row.output["failed_deploys"]) == (1, 1)


def test_a_direct_push_gets_the_fallback_tier_and_subject_0(gh, engine, policy):
    push = "d" * 40
    _ci(gh, push)
    gh.add_workflow_run(DEPLOY_WORKFLOW, push)

    _runner(gh, engine, policy).poll_once(NOON)

    [row] = _rows(engine)
    assert (row.subject_id, row.head_sha, row.tier) == (0, push, policy.fallback_tier)
    assert row.output["prs"] == [] and row.output["modules"] == []
    assert ("GET", f"/commits/{push}/pulls") in gh.requests


def test_a_merge_the_store_lacks_is_found_from_its_commit(gh, engine, policy):
    _release(gh)
    gh.prs[40]["closed_at"] = None  # not in the recently closed page
    gh.prs[40]["created_at"] = "2000-01-01T00:00:00Z"
    for number in range(100, 125):
        _merged(gh, number, f"{number}".rjust(40, "e"), NOON - timedelta(minutes=number))

    _runner(gh, engine, policy).poll_once(NOON)

    assert ("GET", f"/commits/{MERGE}/pulls") in gh.requests
    assert _rows(engine)[0].subject_id == 40


def test_the_tier_is_the_gate_status_then_the_risk_decision_then_the_rubric(gh, engine, policy):
    for number in (1, 2, 3):
        gh.add_pr(number, str(number) * 40, files=("README.md",))
    runner = _runner(gh, engine, policy)
    with Session(engine) as db:
        collector = Collector(db, gh)
        gated, decided, scored = (collector.collect_pull_request(gh.prs[n]) for n in (1, 2, 3))
        for pr, tier in ((gated, "T3"), (decided, "T3")):
            record_decision(
                db,
                agent="pr_risk",
                agent_version="v1",
                subject_type="pr",
                subject_source="github",
                subject_id=pr.number,
                trigger="poll",
                now=NOON,
                tier=tier,
            )
        for status, trigger in (("error", "poll"), ("ok", "trial")):  # neither counts
            record_decision(
                db,
                agent="pr_risk",
                agent_version="v1",
                subject_type="pr",
                subject_source="github",
                subject_id=decided.number,
                trigger=trigger,
                now=NOON + timedelta(minutes=1),
                attempt=2 if status == "error" else 3,
                tier="T0",
                status=status,
            )
        db.add(
            GateStatus(
                pull_request_id=gated.id,
                tier="T1",
                state="success",
                would_be="success",
                missing=[],
                description="T1",
                mode="enforce",
                updated_at=NOON,
            )
        )
        db.flush()
        rubric = assign_tier(policy, score_pull_request(db, scored).total, facts_of(scored)).tier

        assert runner.tier_of(db, gated) == "T1"
        assert runner.tier_of(db, decided) == "T3"
        assert runner.tier_of(db, scored) == rubric == "T0"


def test_a_restart_posts_once_more_and_records_nothing_new(gh, engine, policy):
    _release(gh)
    _incident(gh)
    _runner(gh, engine, policy).poll_once(NOON)

    restarted = _runner(gh, engine, policy)
    assert restarted.poll_once(NOON + timedelta(minutes=1))["assessed"] == 0
    restarted.poll_once(NOON + timedelta(minutes=2))

    assert [s["description"] for s in _gates(gh)] == [HELD, HELD]
    assert len(_rows(engine)) == 1


def test_a_failed_post_is_tried_again_on_the_next_poll(gh, engine, policy):
    _release(gh)
    _incident(gh)
    runner = _runner(gh, engine, policy)
    gh.fail_writes = {"POST"}
    runner.poll_once(NOON)
    [row] = _rows(engine)
    assert "error" in row.action_taken

    gh.fail_writes = set()
    runner.poll_once(NOON + timedelta(minutes=1))

    assert [s["description"] for s in _gates(gh)] == [HELD]
    assert len(_rows(engine)) == 1


def test_a_github_error_on_one_release_is_counted_and_the_rest_go_on(gh, engine, policy, caplog):
    _release(gh)
    gh.add_workflow_run(DEPLOY_WORKFLOW, "bad" + "0" * 37)
    original = gh.paginate

    def paginate(path, key=None, **params):
        if path.startswith("/repos/{repo}/commits/bad"):
            raise release_runner.GitHubError("GitHub 502 on /commits: Bad Gateway")
        return original(path, key, **params)

    gh.paginate = paginate

    summary = _runner(gh, engine, policy).poll_once(NOON)

    assert summary == {"mode": "enforce", "releases": 2, "assessed": 1, "errors": 1}
    assert "release bad0000: GitHub 502" in caplog.text
    assert len(_gates(gh)) == 1


def test_once_prints_the_summary(gh, engine, policy, capsys):
    runner = _runner(gh, engine, policy, "off")

    assert release_runner.main(["once"], runner=runner) == 0
    assert json.loads(capsys.readouterr().out) == {"mode": "off"}
