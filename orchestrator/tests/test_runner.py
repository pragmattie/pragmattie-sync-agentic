import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import approver as approver_module
from sdlc import runner as runner_module
from sdlc.agents.llm import LLMError
from sdlc.agents.signoff import COMMENT_MARKER, WINDOW_AGENT
from sdlc.agents.suite_select import CI_SUITES
from sdlc.approver import load_approvers
from sdlc.db import Base
from sdlc.github_client import GitHubError
from sdlc.runner import Runner
from sdlc.signals.github import SUITES
from sdlc.tables import AgentDecision, Approval, GateStatus, PullRequest
from sdlc.tiers import load_policy
from tests.fakes import BOT, PERSON, WORKFLOW_BOT, FakeGitHub, FakeLLM, iso

NOON = datetime(2026, 10, 6, 12, 0)
SHA = "a" * 40
NEW_SHA = "b" * 40
T1_SIZE = 200  # additions that land the default fake PR in T1
PIPELINE = ("apps/api/app/routers/pipeline.py",)  # floored at T2
MIGRATION = ("orchestrator/migrations/versions/0009_more.py",)  # floored at T3


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
    [simulated] = load_approvers(approver_module.POLICY)
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


def test_observe_decides_and_records_like_shadow_and_writes_nothing(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    summary = _runner(gh, engine, policy, "observe").poll_once(NOON)

    assert summary == {"mode": "observe", "prs": 1, "assessed": 1, "failed": 0, "errors": 0}
    assert gh.writes() == []
    [row] = _rows(engine)
    assert (row.status, row.tier) == ("ok", "T2")
    comment = row.action_taken["comment"]
    assert comment["skipped"] == "mode is observe"
    assert "> **Observe mode.** Nothing is posted to GitHub" in comment["body"]
    assert "It would be: **pending**." in comment["body"]
    assert row.action_taken["label"] == {"skipped": "mode is observe"}
    assert row.action_taken["status"] == {"skipped": "mode is observe"}
    [selection] = _rows(engine, "test_selector")
    assert selection.head_sha == SHA
    gate = _gate(engine, 1)
    assert (gate.state, gate.would_be, gate.mode) == ("success", "pending", "observe")
    assert gate.description.startswith("Observe (would be pending). T2")


def test_observe_notes_the_skipped_status_once_a_commit_and_never_repairs_or_rerenders(
    gh, engine, policy, caplog
):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy, "observe")
    caplog.set_level(logging.INFO, logger="sdlc.runner")

    for minute in range(4):
        runner.poll_once(NOON + timedelta(minutes=minute))
    gh.comment(1, "LGTM")  # a change on the PR doesn't note it again
    runner.poll_once(NOON + timedelta(minutes=5))
    gh.push(1, NEW_SHA)
    for minute in range(6, 9):
        runner.poll_once(NOON + timedelta(minutes=minute))

    noted = [record for record in caplog.records if "observe mode, so risk-gate" in record.message]
    assert [record.args[1] for record in noted] == [SHA[:7], NEW_SHA[:7]]
    assert gh.writes() == []
    assert len(_rows(engine)) == 2  # scored once per commit
    assert _gate(engine, 1).description.startswith("Observe (would be pending). T2")


def _v1_comment(gh, number, sha, signoff=" ", qa=" "):
    """v1's risk comment: the same marker as v2's, written by v1 for ``sha``."""
    body = (
        f"{COMMENT_MARKER}\n<!-- head:{sha} -->\n## Risk review: T2 (Standard)\n\n"
        f"- [{signoff}] **Human sign-off** (senior engineer)\n"
        f"- [{qa}] **Manual QA done**\n"
    )
    return gh.comment(number, body, user=BOT)


def test_observe_counts_the_boxes_in_v1s_comment_whatever_commit_it_names(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy, "observe")
    runner.poll_once(NOON)
    assert _gate(engine, 1).would_be == "pending"
    v1 = _v1_comment(gh, 1, "c" * 40, signoff="x", qa="x")  # v1's head isn't v2's commit
    before = v1["body"]

    runner.poll_once(NOON + timedelta(minutes=1))

    gate = _gate(engine, 1)
    assert (gate.state, gate.would_be) == ("success", "success")
    assert gh.writes() == []
    assert v1["body"] == before  # never refreshed or rendered again


def test_observe_never_treats_v1s_comment_for_this_commit_as_its_own(gh, engine, policy):
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy, "observe")
    runner.poll_once(NOON)
    _v1_comment(gh, 1, SHA)  # unticked, and naming the commit v2 scored
    runner.poll_once(NOON + timedelta(minutes=1))
    gh.push(1, NEW_SHA)

    runner.poll_once(NOON + timedelta(minutes=2))
    runner.poll_once(NOON + timedelta(minutes=3))

    assert _gate(engine, 1).would_be == "pending"
    assert gh.writes() == []


def test_observe_rules_on_a_tier_command_and_records_it_without_writing(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy, "observe")
    runner.poll_once(NOON)
    gh.comment(1, "/tier T3 this touches the forecast maths")

    runner.poll_once(NOON + timedelta(minutes=1))

    [ruling] = _overrides(engine)
    assert (ruling.tier, ruling.status) == ("T3", "ok")
    assert [row.trigger for row in _selector(engine)] == ["poll", "tier_change"]
    assert _gate(engine, 1).tier == "T3"
    assert gh.writes() == []


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


MIXED_FILES = ("apps/api/app/main.py", "orchestrator/sdlc/clock.py", "docs/operating-guide.md")


@pytest.fixture
def every_path(policy):
    window = replace(policy.objection_window, minutes=15, paths=("*",))
    return replace(policy, objection_window=window)


def test_with_every_path_the_window_passes_after_15_minutes_on_a_mixed_pr(gh, engine, every_path):
    gh.add_pr(1, SHA, files=MIXED_FILES, additions=T1_SIZE)
    runner = _runner(gh, engine, every_path)
    runner.poll_once(NOON)
    assert gh.issue_labels[1] == ["tier:T1"]
    _review_comment(gh, 1, SHA, at=NOON + timedelta(minutes=1))
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1]["state"] == "pending"

    runner.poll_once(NOON + timedelta(minutes=16))
    assert gh.statuses[-1]["state"] == "success"
    [window] = _rows(engine, WINDOW_AGENT)
    assert window.output["paths"] == ["*"]
    assert window.output["covers"] == "every file"


def test_with_every_path_the_gate_says_the_window_covers_every_file(gh, engine, every_path):
    gh.add_pr(1, SHA, files=MIXED_FILES, additions=T1_SIZE)
    runner = _runner(gh, engine, every_path)
    runner.poll_once(NOON)
    _review_comment(gh, 1, SHA, at=NOON + timedelta(minutes=1))
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1]["description"] == (
        "T1: waiting for human sign-off or, unless someone comments /hold, merges after 12:16 UTC"
        " (window covers every file)"
    )


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


def _comment_writes(gh, number=1):
    return [
        method
        for method, path in gh.writes()
        if path == f"/issues/{number}/comments" or path.startswith("/issues/comments/")
    ]


def test_with_a_persons_token_two_polls_post_the_risk_comment_once(gh, engine, policy):
    gh.as_person("geoffsmattie")
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    runner.poll_once(NOON + timedelta(minutes=1))
    gh.push(1, NEW_SHA)  # a new commit rewrites the same comment
    runner.poll_once(NOON + timedelta(minutes=2))

    writes = _comment_writes(gh)
    assert writes[0] == "POST"
    assert writes.count("POST") == 1
    assert set(writes[1:]) <= {"PATCH"}
    [comment] = [c for c in gh.comments if c["number"] == 1]
    assert comment["user"]["login"] == "geoffsmattie"
    assert f"<!-- head:{NEW_SHA} -->" in comment["body"]


def test_with_a_persons_token_their_ticks_pass_a_t3_pr(gh, engine, policy, approver):
    gh.as_person("geoffsmattie")
    gh.add_pr(1, SHA, files=MIGRATION)
    runner = _runner(gh, engine, policy, approver=approver)
    runner.poll_once(NOON)
    with Session(engine) as db:  # the owner's explicit instruction, as the CLI would carry out
        pr = db.scalar(select(PullRequest).where(PullRequest.number == 1))
        approver_module.approve(db, pr, approver, now=NOON + timedelta(minutes=1))
        db.commit()
    comment = gh.risk_comment(1)
    comment["body"] = (
        comment["body"]
        .replace("- [ ] **Human sign-off", "- [x] **Human sign-off")
        .replace("- [ ] **Manual QA done", "- [x] **Manual QA done")
    )
    runner.poll_once(NOON + timedelta(minutes=2))
    assert gh.statuses[-1]["state"] == "success"
    assert _gate(engine, 1).state == "success"
    assert _comment_writes(gh).count("POST") == 1


def test_another_users_ticked_copy_of_the_risk_comment_is_ignored(gh, engine, policy):
    gh.as_person("geoffsmattie")
    gh.add_pr(1, SHA, files=PIPELINE)
    forged_body = (
        f"{COMMENT_MARKER}\n<!-- head:{SHA} -->\n- [x] **Human sign-off** (senior engineer)\n"
    )
    forged = gh.comment(1, forged_body, user=PERSON)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    runner.poll_once(NOON + timedelta(minutes=1))
    assert gh.statuses[-1]["state"] == "pending"
    assert forged["body"] == forged_body
    assert _comment_writes(gh)[0] == "POST"


def test_observe_reads_the_ticks_in_v1s_comment_posted_under_the_same_login(gh, engine, policy):
    me = gh.as_person("geoffsmattie")
    gh.add_pr(1, SHA, files=PIPELINE)
    runner = _runner(gh, engine, policy, "observe")
    runner.poll_once(NOON)
    assert _gate(engine, 1).would_be == "pending"
    v1 = _v1_comment(gh, 1, "c" * 40, signoff="x", qa="x")
    v1["user"] = dict(me)

    runner.poll_once(NOON + timedelta(minutes=1))

    assert _gate(engine, 1).would_be == "success"
    assert gh.writes() == []
    assert ("GET", "/user") in gh.requests


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


def _selector(engine):
    return _rows(engine, "test_selector")


def test_the_risk_comment_carries_the_tests_section_and_one_poll_row(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    _runner(gh, engine, policy).poll_once(NOON)
    body = gh.risk_comment(1)["body"]
    assert "<!-- tests -->\n**Tests** (the test selector's recommendation" in body
    assert "- Would run: **CRM web**" in body
    assert "  - CRM web: code changed in apps/crm-web/src/App.vue" in body
    assert "- Would skip: CRM API, Orchestrator, Migrations, Insights web" in body
    [row] = _selector(engine)
    assert (row.trigger, row.head_sha, row.attempt, row.tier) == ("poll", SHA, 1, "T1")
    assert row.output["suites"] == ["web"]
    assert row.output["paths"] == ["apps/crm-web/src/App.vue"]
    assert ("GET", "/pulls/1/files") in gh.requests
    assert not [path for _, path in gh.writes() if "/actions" in path]


def test_a_tier_t3_command_writes_one_tier_change_row_and_rewrites_the_section(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    gh.comment(1, "/tier T3 this touches the forecast maths")
    runner.poll_once(NOON + timedelta(minutes=1))
    runner.poll_once(NOON + timedelta(minutes=2))  # the tier stays the same: no new row
    poll, change = _selector(engine)
    assert (change.trigger, change.attempt, change.tier) == ("tier_change", 2, "T3")
    assert change.output["suites"] == ["api", "orchestrator", "migrations", "web", "insights-web"]
    assert change.output["paths"] == poll.output["paths"]
    body = gh.risk_comment(1)["body"]
    assert "- Would run every job: T3 runs the full suite (full suite + manual QA)." in body
    assert "- Would skip" not in body
    assert body.count("<!-- tests -->") == 1
    assert "## Risk review: T3 (Critical), raised from T1 by a person" in body
    assert not [path for _, path in gh.writes() if "/actions" in path]


def test_a_tier_command_while_the_comment_is_for_an_older_commit_writes_no_tier_change_row(
    gh, engine, policy
):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    gh.push(1, NEW_SHA)
    gh.fail_writes = {"POST", "PATCH"}  # the risk comment stays on the older commit
    runner.poll_once(NOON + timedelta(minutes=1))
    gh.comment(1, "/tier T3 this touches the forecast maths")
    runner.poll_once(NOON + timedelta(minutes=2))
    assert f"<!-- head:{SHA} -->" in gh.risk_comment(1)["body"]
    assert [row.trigger for row in _selector(engine)] == ["poll", "poll"]


def test_a_failed_then_successful_assessment_writes_two_selector_rows(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    llm = FakeLLM(error=LLMError("timeout", "slow"))
    runner = _runner(gh, engine, policy, llm=llm)
    runner.poll_once(NOON)
    assert (
        "- Would run every job: the risk agent couldn't score this commit"
        in (gh.risk_comment(1)["body"])
    )
    llm.error = None
    runner.poll_once(NOON + timedelta(minutes=5))
    failed, scored = _selector(engine)
    assert (failed.attempt, failed.output["full"]) == (
        1,
        "the risk agent couldn't score this commit, so nothing is skipped",
    )
    assert len(failed.output["suites"]) == 5
    assert (scored.attempt, scored.tier, scored.output["suites"]) == (2, "T1", ["web"])
    assert "- Would run: **CRM web**" in gh.risk_comment(1)["body"]
    assert not [path for _, path in gh.writes() if "/actions" in path]


def test_a_repaired_risk_comment_keeps_the_tests_section_without_a_new_row(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    runner = _runner(gh, engine, policy)
    gh.fail_writes = {"POST"}
    runner.poll_once(NOON)
    gh.fail_writes = set()
    runner.poll_once(NOON + timedelta(minutes=1))
    assert "- Would run: **CRM web**" in gh.risk_comment(1)["body"]
    assert len(_selector(engine)) == 1


JOB_NAMES = {suite: name for name, suite in SUITES.items()}  # CI's job name for each suite


def _ci_run(gh, sha=SHA, running=(), failed=()):
    """One CI run on the commit: 90 seconds per job, ``running`` jobs not finished yet."""
    jobs = []
    for suite in CI_SUITES:
        done = suite not in running
        jobs.append(
            {
                "name": JOB_NAMES[suite],
                "status": "completed" if done else "in_progress",
                "conclusion": ("failure" if suite in failed else "success") if done else None,
                "run_attempt": 1,
                "started_at": iso(NOON - timedelta(minutes=10)),
                "completed_at": iso(NOON - timedelta(minutes=10, seconds=-90)) if done else None,
            }
        )
    return gh.add_run(sha, jobs)


def _ci_results(engine):
    return [row for row in _selector(engine) if row.trigger == "ci_result"]


def test_a_commit_is_graded_once_every_ci_job_has_finished(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    run = _ci_run(gh, running=("api",))
    runner = _runner(gh, engine, policy)
    summary = runner.poll_once(NOON)
    assert summary["errors"] == 0
    assert _ci_results(engine) == []
    api = gh.jobs[run["id"]][0]
    api.update(
        status="completed", conclusion="success", completed_at=iso(NOON - timedelta(minutes=8.5))
    )
    runner.poll_once(NOON + timedelta(minutes=2))
    [row] = _ci_results(engine)
    assert (row.status, row.attempt, row.tier) == ("ok", 2, "T1")
    assert row.output["missed"] == []
    assert row.output["saved_seconds"] == 4 * 90
    for minutes in (4, 6, 8):
        runner.poll_once(NOON + timedelta(minutes=minutes))
    assert len(_ci_results(engine)) == 1
    assert not [path for _, path in gh.writes() if "/actions" in path]


def test_a_failed_skipped_job_is_recorded_as_missed(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    _ci_run(gh, failed=("api",))
    _runner(gh, engine, policy).poll_once(NOON)
    [row] = _ci_results(engine)
    assert (row.status, row.output["missed"]) == ("missed", ["api"])


def _runs_read(gh):
    return len([path for _, path in gh.requests if path == "/actions/runs"])


def test_settling_runs_at_most_every_two_minutes(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    _ci_run(gh, running=("web",))
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    assert _runs_read(gh) == 1
    runner.poll_once(NOON + timedelta(seconds=60))
    assert _runs_read(gh) == 1
    runner.poll_once(NOON + timedelta(seconds=120))
    assert _runs_read(gh) == 2


def test_a_github_error_while_settling_is_counted_and_waits(gh, engine, policy, caplog):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    _ci_run(gh)
    fetch = gh.get

    def failing(path, **params):
        if path.endswith("/actions/runs"):
            fetch(path, **params)
            raise GitHubError("GitHub 502 on /actions/runs: bad gateway")
        return fetch(path, **params)

    gh.get = failing
    runner = _runner(gh, engine, policy)
    with caplog.at_level(logging.WARNING, "sdlc.runner"):
        summary = runner.poll_once(NOON)
    assert summary["errors"] == 1
    assert "test selector: GitHub 502 on /actions/runs: bad gateway" in caplog.text
    assert _ci_results(engine) == []
    assert len(_selector(engine)) == 1  # the recommendation itself was kept
    gh.get = fetch
    runner.poll_once(NOON + timedelta(minutes=1))
    assert _ci_results(engine) == []
    runner.poll_once(NOON + timedelta(minutes=2))
    assert len(_ci_results(engine)) == 1


def test_a_malformed_recommendation_while_settling_is_counted_and_the_poll_finishes(
    gh, engine, policy, caplog
):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    run = _ci_run(gh, running=("api",))
    runner = _runner(gh, engine, policy)
    runner.poll_once(NOON)
    with Session(engine) as db:
        row = db.get(AgentDecision, _selector(engine)[0].id)
        row.output = {key: value for key, value in row.output.items() if key != "skipped"}
        db.commit()
    gh.jobs[run["id"]][0].update(status="completed", conclusion="success")
    with caplog.at_level(logging.ERROR, "sdlc.runner"):
        summary = runner.poll_once(NOON + timedelta(minutes=2))
    assert (summary["prs"], summary["errors"]) == (1, 1)
    assert "test selector failed" in caplog.text
    assert _ci_results(engine) == []


def test_off_never_reads_ci_results(gh, engine, policy):
    gh.add_pr(1, SHA, additions=T1_SIZE)
    _ci_run(gh)
    _runner(gh, engine, policy, mode="off").poll_once(NOON)
    assert gh.requests == []
