import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import approver as approver_module
from sdlc import runner as runner_module
from sdlc.agents.llm import LLMError
from sdlc.agents.signoff import WINDOW_AGENT
from sdlc.approver import load_approvers
from sdlc.db import Base
from sdlc.runner import Runner
from sdlc.tables import AgentDecision, Approval, GateStatus, PullRequest
from sdlc.tiers import load_policy
from tests.fakes import WORKFLOW_BOT, FakeGitHub, FakeLLM

NOON = datetime(2026, 10, 6, 12, 0)
SHA = "a" * 40
NEW_SHA = "b" * 40
T1_SIZE = 200  # additions that land the default fake PR in T1
PIPELINE = ("apps/api/app/routers/pipeline.py",)  # floored at T2
MIGRATION = ("orchestrator/migrations/versions/0009_more.py",)  # floored at T3
# Read only through an explicit path until a person moves it to orchestrator/policies/.
APPROVERS = Path(__file__).resolve().parents[1] / "policies" / "approvers.yaml"


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


@pytest.fixture(scope="module")
def approver():
    [simulated] = load_approvers(APPROVERS)
    return simulated


def _runner(gh, engine, policy, mode="enforce", llm=None, approver=None):
    return Runner(gh, llm or FakeLLM(), policy, mode, engine=engine, approver=approver)


def _rows(engine, agent="pr_risk"):
    with Session(engine) as db:
        return list(
            db.scalars(
                select(AgentDecision).where(AgentDecision.agent == agent).order_by(AgentDecision.id)
            )
        )


def _gate(engine, number):
    with Session(engine) as db:
        return db.scalar(
            select(GateStatus)
            .join(PullRequest, GateStatus.pull_request_id == PullRequest.id)
            .where(PullRequest.number == number)
        )


def _tick(gh, number):
    comment = gh.risk_comment(number)
    comment["body"] = comment["body"].replace("- [ ] **Human sign-off", "- [x] **Human sign-off")


def _review_comment(gh, number, sha, verdict="approve", run=77, at=NOON):
    record = {"pr": number, "run": run, "verdict": verdict, "sha": sha}
    gh.comment(number, f"<!-- pragmattie-review {json.dumps(record)} -->", user=WORKFLOW_BOT, at=at)


def test_off_makes_no_request_at_all(gh, engine, policy):
    gh.add_pr(1, SHA)
    llm = FakeLLM()
    assert _runner(gh, engine, policy, "off", llm).poll_once(NOON) == {"mode": "off"}
    assert gh.requests == []
    assert llm.calls == []
    assert _rows(engine) == []


def test_shadow_posts_success_saying_what_it_would_be(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    summary = _runner(gh, engine, policy, "shadow").poll_once(NOON)
    assert summary == {"mode": "shadow", "prs": 1, "assessed": 1, "failed": 0, "errors": 0}
    [status] = gh.statuses
    assert status["state"] == "success"
    assert status["description"].startswith("Shadow mode (would be pending). T2")
    assert gh.issue_labels[1] == ["tier:T2"]
    assert "**Shadow mode.**" in gh.risk_comment(1)["body"]
    gate = _gate(engine, 1)
    assert (gate.state, gate.would_be, gate.mode) == ("success", "pending", "shadow")


def test_a_new_commit_writes_comment_label_and_status_in_order_and_records_them(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    _runner(gh, engine, policy).poll_once(NOON)
    writes = [path for method, path in gh.writes()]
    assert writes.index("/issues/1/comments") < writes.index("/issues/1/labels")
    assert writes.index("/issues/1/labels") < writes.index(f"/statuses/{SHA}")
    [row] = _rows(engine)
    assert (row.trigger, row.attempt, row.head_sha, row.status) == ("poll", 1, SHA, "ok")
    assert row.created_at == NOON
    assert row.action_taken == {
        "comment": {"done": "comment"},
        "label": {"done": "tier:T2"},
        "status": {"done": "risk-gate pending"},
    }
    assert f"decision {row.id}" in gh.risk_comment(1)["body"]


def test_enforce_is_pending_until_the_box_is_ticked_for_that_commit(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    assert gh.statuses[-1]["state"] == "pending"
    _tick(gh, 1)
    runner.poll_once(NOON + timedelta(minutes=1))
    assert gh.statuses[-1]["state"] == "success"
    assert gh.statuses[-1]["description"] == "T2: requirements met"
    assert _gate(engine, 1).state == "success"

    gh.push(1, NEW_SHA)  # a new commit starts with nothing signed off
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1] == {
        "sha": NEW_SHA,
        "state": "pending",
        "description": "T2: waiting for human sign-off",
        "context": "risk-gate",
    }
    assert "- [ ] **Human sign-off" in gh.risk_comment(1)["body"]


def test_enforce_passes_once_a_person_approves_that_commit_on_github(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    gh.review(1, "c" * 40)  # an older commit doesn't count
    runner.poll_once(NOON + timedelta(minutes=1))
    assert gh.statuses[-1]["state"] == "pending"
    gh.review(1, SHA)
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1]["state"] == "success"


def test_a_new_commit_is_assessed_once(gh, engine, policy):
    gh.add_pr(1, SHA)
    llm = FakeLLM()
    runner = _runner(gh, engine, policy, llm=llm)
    runner.poll_once(NOON)
    summary = runner.poll_once(NOON + timedelta(minutes=10))
    assert len(llm.calls) == 1
    assert summary["assessed"] == 0
    assert len(_rows(engine)) == 1


def test_three_failures_five_minutes_apart_then_no_more_and_the_gate_stays_failed(
    gh, engine, policy
):
    gh.add_pr(1, SHA)
    llm = FakeLLM(error=LLMError("timeout", "slow"))
    runner = _runner(gh, engine, policy, llm=llm)
    for minute in (0, 2, 5, 7, 10, 15, 20, 60):
        summary = runner.poll_once(NOON + timedelta(minutes=minute))
        assert gh.statuses[-1]["state"] == "failure", minute
        assert _gate(engine, 1).state == "failure"
        assert summary["errors"] == 0
    assert len(llm.calls) == 3
    rows = _rows(engine)
    assert [row.attempt for row in rows] == [1, 2, 3]
    assert [row.created_at for row in rows] == [NOON + timedelta(minutes=m) for m in (0, 5, 10)]
    assert {row.status for row in rows} == {"timeout"}
    assert {row.tier for row in rows} == {"T2"}  # the policy's fallback tier


def test_an_exception_inside_assess_is_a_failed_decision_and_a_failing_gate(gh, engine, policy):
    gh.add_pr(1, SHA)
    llm = FakeLLM(error=RuntimeError("boom"))
    summary = _runner(gh, engine, policy, llm=llm).poll_once(NOON)
    assert summary == {"mode": "enforce", "prs": 1, "assessed": 1, "failed": 1, "errors": 0}
    [row] = _rows(engine)
    assert row.status == "error"
    assert row.error == "RuntimeError: boom"
    assert gh.statuses[-1]["state"] == "failure"
    assert "could not score this PR" in gh.risk_comment(1)["body"]


def test_a_t0_pr_passes_only_after_a_reviewer_approve_on_that_exact_commit(gh, engine, policy):
    gh.add_pr(1, SHA)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    assert gh.issue_labels[1] == ["tier:T0"]
    assert gh.statuses[-1]["description"] == "T0: waiting for AI review approval"

    _review_comment(gh, 1, "c" * 40, run=1, at=NOON + timedelta(seconds=30))
    runner.poll_once(NOON + timedelta(minutes=1))
    assert gh.statuses[-1]["state"] == "pending"

    # recorded before the gate is computed, so it counts on the same poll
    _review_comment(gh, 1, SHA, run=2, at=NOON + timedelta(minutes=1, seconds=30))
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1]["state"] == "success"
    assert len(_rows(engine, "reviewer")) == 2


def test_the_first_poll_reads_run_records_from_the_last_30_days(gh, engine, policy):
    record = {"issue": 9, "run": 5, "outcome": "success", "tests_passed": "true"}
    body = f"<!-- pragmattie-run {json.dumps(record)} -->"
    gh.comment(9, body, user=WORKFLOW_BOT, at=NOON - timedelta(days=29))
    gh.comment(
        9, body.replace('"run": 5', '"run": 4'), user=WORKFLOW_BOT, at=NOON - timedelta(days=31)
    )
    gh.comment(9, body.replace('"run": 5', '"run": 3'), at=NOON - timedelta(days=1))  # a person
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    runner.poll_once(NOON + timedelta(minutes=1))
    assert [row.head_sha for row in _rows(engine, "implementer")] == ["run-5"]


def test_the_objection_window_passes_after_60_minutes_with_one_audit_row(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    assert gh.issue_labels[1] == ["tier:T1"]
    _review_comment(gh, 1, SHA, at=NOON + timedelta(minutes=1))
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1]["state"] == "pending"
    assert "merges after 13:01 UTC" in gh.statuses[-1]["description"]

    for minute in (61, 62, 90):
        runner.poll_once(NOON + timedelta(minutes=minute))
        assert gh.statuses[-1]["state"] == "success"
    [window] = _rows(engine, WINDOW_AGENT)
    assert window.head_sha == f"window-{SHA}"[:40]
    assert window.created_at == NOON + timedelta(minutes=61)


def test_a_hold_stops_the_objection_window(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    _review_comment(gh, 1, SHA, at=NOON + timedelta(minutes=1))
    gh.comment(1, "/hold I want to look at this")
    runner.poll_once(NOON + timedelta(minutes=90))
    assert gh.statuses[-1]["state"] == "pending"
    assert "merges after" not in gh.statuses[-1]["description"]
    assert _rows(engine, WINDOW_AGENT) == []


def test_a_merged_pr_is_collected_once_more_and_stored_as_merged(gh, engine, policy):
    gh.add_pr(1, SHA)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    gh.merge(1, NOON + timedelta(minutes=1))
    gh.requests.clear()
    runner.poll_once(NOON + timedelta(minutes=2))
    assert ("GET", "/pulls/1") in gh.requests
    with Session(engine) as db:
        pr = db.scalar(select(PullRequest).where(PullRequest.number == 1))
        assert pr.state == "merged"
        assert pr.merged_at == NOON + timedelta(minutes=1)
    gh.requests.clear()
    runner.poll_once(NOON + timedelta(minutes=3))
    assert ("GET", "/pulls/1") not in gh.requests  # only once


def test_drafts_are_skipped(gh, engine, policy):
    gh.add_pr(1, SHA, draft=True)
    llm = FakeLLM()
    summary = _runner(gh, engine, policy, llm=llm).poll_once(NOON)
    assert summary["prs"] == 0
    assert llm.calls == []
    assert gh.writes() == []


def test_an_unchanged_status_isnt_posted_twice_and_the_comment_isnt_rewritten(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy, "shadow")
    runner.poll_once(NOON)
    for minute in (1, 2, 3):
        runner.poll_once(NOON + timedelta(minutes=minute))
    assert len(gh.statuses) == 1
    assert [method for method, _ in gh.writes()].count("PATCH") == 0
    assert _gate(engine, 1).updated_at == NOON + timedelta(minutes=3)  # always upserted


def test_the_shadow_comment_is_refreshed_when_its_would_be_changes(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy, "shadow")
    runner.poll_once(NOON)
    _tick(gh, 1)
    runner.poll_once(NOON + timedelta(minutes=1))
    assert "enforce mode would do: **success**" in gh.risk_comment(1)["body"]
    assert gh.statuses[-1]["description"].startswith("Shadow mode (would be success)")


def test_one_prs_exception_is_counted_and_the_loop_moves_on(gh, engine, policy, monkeypatch):
    gh.add_pr(1, SHA)
    gh.add_pr(2, NEW_SHA, files=PIPELINE)
    broken = gh.get_text

    def get_text(path, accept="application/vnd.github.diff"):
        if path.endswith("/pulls/1"):
            raise RuntimeError("diff unavailable")
        return broken(path, accept)

    monkeypatch.setattr(gh, "get_text", get_text)
    summary = _runner(gh, engine, policy).poll_once(NOON)
    assert summary == {"mode": "enforce", "prs": 2, "assessed": 1, "failed": 0, "errors": 1}
    assert gh.statuses_for(SHA) == []  # left as it was: no status, so still gated
    assert len(gh.statuses_for(NEW_SHA)) == 1
    assert [row.subject_id for row in _rows(engine)] == [2]


def test_a_failed_status_write_is_recorded_and_posted_again_next_poll(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    gh.fail_writes = {"POST"}
    runner.poll_once(NOON)
    [row] = _rows(engine)
    assert set(row.action_taken["status"]) == {"error"}
    assert set(row.action_taken["comment"]) == {"error"}
    gh.fail_writes = set()
    runner.poll_once(NOON + timedelta(minutes=1))
    assert [status["state"] for status in gh.statuses] == ["pending"]


def test_the_reviewers_comment_time_is_kept_as_utc(gh, engine, policy):
    gh.add_pr(1, SHA)
    _review_comment(gh, 1, SHA, at=NOON - timedelta(hours=1))
    _runner(gh, engine, policy).poll_once(NOON)
    [review] = _rows(engine, "reviewer")
    assert review.created_at == NOON - timedelta(hours=1)


def test_a_risk_comment_and_tier_label_that_failed_to_post_are_repaired_next_poll(
    gh, engine, policy
):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    gh.fail_writes = {"POST"}
    runner.poll_once(NOON)
    assert gh.risk_comment(1) is None
    assert gh.issue_labels.get(1) is None
    gh.fail_writes = set()

    runner.poll_once(NOON + timedelta(minutes=1))
    [row] = _rows(engine)
    body = gh.risk_comment(1)["body"]
    assert f"<!-- head:{SHA} -->" in body
    assert "## Risk review: T2" in body
    assert "- [ ] **Human sign-off" in body
    assert f"decision {row.id}" in body
    assert gh.issue_labels[1] == ["tier:T2"]
    assert gh.statuses[-1]["state"] == "pending"

    gh.requests.clear()
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.writes() == []  # once repaired, nothing is written again


def test_a_risk_comment_left_on_an_older_commit_is_rewritten_for_this_one(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    _tick(gh, 1)
    gh.push(1, NEW_SHA)
    gh.fail_writes = {"POST", "PATCH"}
    runner.poll_once(NOON + timedelta(minutes=1))
    assert f"<!-- head:{SHA} -->" in gh.risk_comment(1)["body"]
    gh.fail_writes = set()

    runner.poll_once(NOON + timedelta(minutes=2))
    body = gh.risk_comment(1)["body"]
    assert f"<!-- head:{NEW_SHA} -->" in body
    assert "- [ ] **Human sign-off" in body  # the older commit's tick doesn't carry over
    assert f"decision {_rows(engine)[-1].id}" in body
    assert len([c for c in gh.comments if c["user"]["type"] == "Bot"]) == 1  # edited in place
    assert gh.statuses[-1] == {
        "sha": NEW_SHA,
        "state": "pending",
        "description": "T2: waiting for human sign-off",
        "context": "risk-gate",
    }


def test_a_wrong_or_missing_tier_label_is_put_right_on_a_later_poll(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    gh.issue_labels[1] = ["tier:T0", "bug"]
    runner.poll_once(NOON + timedelta(minutes=1))
    assert gh.issue_labels[1] == ["bug", "tier:T2"]

    gh.issue_labels[1] = ["bug"]
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.issue_labels[1] == ["bug", "tier:T2"]

    gh.issue_labels[1] = ["tier:T2", "tier:T3"]
    runner.poll_once(NOON + timedelta(minutes=3))
    assert gh.issue_labels[1] == ["tier:T2"]

    gh.requests.clear()
    runner.poll_once(NOON + timedelta(minutes=4))
    assert gh.writes() == []


def _overrides(engine):
    return _rows(engine, "tier_override")


def test_a_tier_t3_comment_on_a_t1_pr_raises_label_gate_and_comment(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    assert gh.issue_labels[1] == ["tier:T1"]
    assert gh.statuses[-1]["description"] == "T1: waiting for human sign-off"
    command = gh.comment(1, "/tier T3 this touches the forecast maths")

    runner.poll_once(NOON + timedelta(minutes=1))
    assert gh.issue_labels[1] == ["tier:T3"]
    assert gh.statuses[-1]["description"] == (
        "T3: waiting for human sign-off, manual QA, simulated second approval"
    )
    assert _gate(engine, 1).tier == "T3"
    body = gh.risk_comment(1)["body"]
    assert "## Risk review: T3 (Critical), raised from T1 by a person" in body
    assert '- `/tier T3` by @pat "this touches the forecast maths": Raised from T1 to T3.' in body
    assert "- [ ] **Manual QA done" in body
    [row] = _overrides(engine)
    assert (row.trigger, row.head_sha, row.tier, row.status) == (
        "human",
        f"comment-{command['id']}",
        "T3",
        "ok",
    )
    assert row.human_override["direction"] == "raise"
    assert row.human_override["head_sha"] == SHA
    assert row.output == {"url": command["html_url"], "floor": "T0"}
    assert _rows(engine)[-1].tier == "T1"  # the agent's own row keeps its tier

    gh.push(1, NEW_SHA)  # the raise sticks on a later commit
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.issue_labels[1] == ["tier:T3"]
    assert gh.statuses[-1]["description"].startswith("T3: waiting for human sign-off")
    assert "raised from T1 by a person" in gh.risk_comment(1)["body"]


def test_a_rejected_lowering_is_shown_in_the_comment_and_keeps_the_tier(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    body = gh.risk_comment(1)["body"]
    gh.comment(1, "/tier T0 - it is a really small change, honestly")
    gh.requests.clear()

    runner.poll_once(NOON + timedelta(minutes=1))
    [row] = _overrides(engine)
    assert (row.status, row.tier) == ("rejected", None)
    assert row.human_override["why"].startswith("Rejected: a policy floor keeps this PR at T2")
    assert row.output["floor"] == "T2"
    assert gh.issue_labels[1] == ["tier:T2"]
    assert _gate(engine, 1).tier == "T2"
    # only the comment changes: the ruling is shown, the tier is not
    [write] = gh.writes()
    assert write[0] == "PATCH"
    after = gh.risk_comment(1)["body"]
    assert after != body
    assert "## Risk review: T2" in after and "by a person" not in after
    assert (
        '- `/tier T0` by @pat "it is a really small change, honestly": Rejected: a policy floor '
        "keeps this PR at T2 or above." in after
    )

    gh.requests.clear()  # a ruling already shown is not written again
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.writes() == []


def test_a_lowering_covers_only_its_commit(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    gh.comment(1, "/tier T0: only a label's wording changes")
    runner.poll_once(NOON + timedelta(minutes=1))
    assert gh.issue_labels[1] == ["tier:T0"]
    assert "lowered from T1 by a person" in gh.risk_comment(1)["body"]

    gh.push(1, NEW_SHA)
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.issue_labels[1] == ["tier:T1"]
    assert "## Risk review: T1 (Light)\n" in gh.risk_comment(1)["body"]


def test_each_comment_is_ruled_on_once_across_polls(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    gh.comment(1, "/tier T2")
    gh.comment(1, "/tier T0 no")
    for minute in (1, 2, 3):
        runner.poll_once(NOON + timedelta(minutes=minute))
    rows = _overrides(engine)
    assert [(row.status, row.human_override["from_tier"]) for row in rows] == [
        ("ok", "T1"),
        ("rejected", "T2"),  # ruled against the tier in force after the raise
    ]
    gh.comment(1, "/tier T3")
    runner.poll_once(NOON + timedelta(minutes=4))
    assert len(_overrides(engine)) == 3
    assert gh.issue_labels[1] == ["tier:T3"]


def _approvals(engine):
    with Session(engine) as db:
        rows = db.scalars(select(Approval).order_by(Approval.id))
        return [(row.pull_request.number, row.tier, row.status, row.decided_at) for row in rows]


def test_a_t3_pr_gets_one_request_across_polls_and_commits(gh, engine, policy, approver):
    gh.add_pr(1, SHA, files=MIGRATION)
    gh.add_pr(2, NEW_SHA, files=PIPELINE)  # T2: not covered
    runner = _runner(gh, engine, policy, approver=approver)
    runner.poll_once(NOON)
    assert gh.issue_labels[1] == ["tier:T3"]
    for minute in (1, 2):
        runner.poll_once(NOON + timedelta(minutes=minute))
    gh.push(1, "c" * 40)
    runner.poll_once(NOON + timedelta(minutes=3))
    assert _approvals(engine) == [(1, "T3", "pending", None)]
    body = gh.risk_comment(1)["body"]
    assert "- Simulated second approval: **waiting** (Simulated second approver," in body


def test_a_tier_t3_raise_on_a_t1_pr_creates_one_request(gh, engine, policy, approver):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy, approver=approver)
    runner.poll_once(NOON)
    assert _approvals(engine) == []
    gh.comment(1, "/tier T3 this touches the forecast maths")
    for minute in (1, 2):
        runner.poll_once(NOON + timedelta(minutes=minute))
    assert _approvals(engine) == [(1, "T3", "pending", None)]
    assert (
        "Simulated second approval: **waiting** (Simulated second approver,"
        in (gh.risk_comment(1)["body"])
    )


def test_enforce_passes_t3_only_with_signoff_qa_and_the_simulated_approval(
    gh, engine, policy, approver
):
    gh.add_pr(1, SHA, files=MIGRATION)
    runner = _runner(gh, engine, policy, approver=approver)
    runner.poll_once(NOON)
    assert gh.statuses[-1]["description"] == (
        "T3: waiting for human sign-off, manual QA, simulated second approval"
    )
    with Session(engine) as db:  # the owner's explicit instruction, as the CLI would carry out
        pr = db.scalar(select(PullRequest).where(PullRequest.number == 1))
        approver_module.approve(db, pr, approver, now=NOON + timedelta(minutes=1))
        db.commit()
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1]["description"] == "T3: waiting for human sign-off, manual QA"
    assert "- Simulated second approval: **approved**" in gh.risk_comment(1)["body"]

    _tick(gh, 1)
    runner.poll_once(NOON + timedelta(minutes=3))
    assert gh.statuses[-1]["description"] == "T3: waiting for manual QA"
    comment = gh.risk_comment(1)
    comment["body"] = comment["body"].replace("- [ ] **Manual QA done", "- [x] **Manual QA done")
    runner.poll_once(NOON + timedelta(minutes=4))
    assert gh.statuses[-1]["state"] == "success"
    assert _gate(engine, 1).state == "success"


def test_nothing_in_the_runner_ever_approves(gh, engine, policy, approver):
    assert not hasattr(runner_module, "approve")
    gh.add_pr(1, SHA, files=MIGRATION)
    runner = _runner(gh, engine, policy, approver=approver)
    _review_comment(gh, 1, SHA)
    for minute in (0, 1, 61, 120):
        if minute == 1:
            _tick(gh, 1)
            gh.review(1, SHA)
        runner.poll_once(NOON + timedelta(minutes=minute))
    assert _approvals(engine) == [(1, "T3", "pending", None)]
    assert gh.statuses[-1]["description"].endswith("simulated second approval")
