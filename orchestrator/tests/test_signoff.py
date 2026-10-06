from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone

import pytest

from sdlc.agents.gate import Approvals, evaluate
from sdlc.agents.signoff import (
    COMMENT_MARKER,
    ai_approval,
    approved_on_github,
    describe_window,
    objection_window,
    ticked,
    window_signoff_fields,
)
from sdlc.tables import AgentDecision, PullRequest
from sdlc.tiers import load_policy

POLICY = load_policy()
SHA, OLD_SHA = "a" * 40, "b" * 40
APPROVED_AT = datetime(2026, 10, 6, 9, 15)


def _review(state, *, login="pat", sha=SHA, kind="User"):
    return {"user": {"login": login, "type": kind}, "state": state, "commit_id": sha}


def _comment(body, *, kind="User"):
    return {"user": {"login": "pat" if kind == "User" else "bot[bot]", "type": kind}, "body": body}


def _risk_comment(sha, *, signoff="x", qa=" "):
    return (
        f"{COMMENT_MARKER}\n<!-- head:{sha} -->\n## Risk: T3\n"
        f"- [{signoff}] **Human sign-off** (tick when you have reviewed this change)\n"
        f"- [{qa}] **Manual QA done** (tick when it has been tried by hand)\n"
    )


def _reviewer_row(verdict, *, sha=SHA, minute=0, row_id=1, status="ok", trigger="poll"):
    return AgentDecision(
        id=row_id,
        agent="reviewer",
        created_at=APPROVED_AT + timedelta(minutes=minute),
        status=status,
        trigger=trigger,
        action_taken={"verdict": verdict, "commit": sha, "comment": None},
    )


# Sign-off boxes


def test_ticked_boxes_count_for_the_commit_the_comment_was_written_for():
    assert ticked(_risk_comment(SHA), SHA) == (True, False)
    assert ticked(_risk_comment(SHA, signoff="X", qa="X"), SHA) == (True, True)
    assert ticked(_risk_comment(SHA, signoff=" "), SHA) == (False, False)


def test_boxes_ticked_for_an_old_commit_dont_count():
    assert ticked(_risk_comment(OLD_SHA, qa="x"), SHA) == (False, False)


def test_boxes_count_only_in_the_risk_comment():
    body = "Copied:\n" + _risk_comment(SHA, qa="x")

    assert ticked(body, SHA) == (False, False)
    assert ticked(None, SHA) == (False, False)


# GitHub reviews


def test_a_persons_approval_of_this_commit_counts():
    assert approved_on_github([_review("APPROVED")], SHA) is True


def test_a_bots_approval_doesnt_count():
    assert approved_on_github([_review("APPROVED", login="ci[bot]", kind="Bot")], SHA) is False


def test_an_approval_on_an_old_commit_doesnt_count():
    assert approved_on_github([_review("APPROVED", sha=OLD_SHA)], SHA) is False


def test_a_later_changes_requested_from_the_same_person_cancels_their_approval():
    reviews = [_review("APPROVED"), _review("CHANGES_REQUESTED")]

    assert approved_on_github(reviews, SHA) is False


def test_a_later_dismissal_cancels_an_approval():
    assert approved_on_github([_review("APPROVED"), _review("DISMISSED")], SHA) is False


@pytest.mark.parametrize("state", ["COMMENTED", "PENDING"])
def test_a_comment_after_an_approval_doesnt_cancel_it(state):
    assert approved_on_github([_review("APPROVED"), _review(state)], SHA) is True


def test_a_review_with_no_user_isnt_a_persons_approval():
    ghost = {"user": None, "state": "APPROVED", "commit_id": SHA}

    assert approved_on_github([ghost], SHA) is False
    assert approved_on_github([ghost, _review("APPROVED")], SHA) is True


def test_another_persons_changes_requested_leaves_an_approval_standing():
    reviews = [_review("APPROVED"), _review("CHANGES_REQUESTED", login="sam")]

    assert approved_on_github(reviews, SHA) is True


# The AI reviewer


def test_ai_approval_is_the_reviewers_approving_row_for_this_commit():
    row = _reviewer_row("approve")

    assert ai_approval([row], SHA) is row


def test_a_newer_request_for_changes_on_the_commit_overrides_an_older_approval():
    rows = [_reviewer_row("approve"), _reviewer_row("request_changes", minute=5, row_id=2)]

    assert ai_approval(rows, SHA) is None


def test_ai_approval_ignores_other_commits_failed_runs_and_trials():
    rows = [
        _reviewer_row("approve", sha=OLD_SHA),
        _reviewer_row("approve", minute=1, row_id=2, status="error"),
        _reviewer_row("approve", minute=2, row_id=3, trigger="trial"),
    ]

    assert ai_approval(rows, SHA) is None
    approved = _reviewer_row("approve", minute=3, row_id=4)
    assert ai_approval([*rows, approved], SHA) is approved


# The objection window


def test_a_t1_pr_touching_only_the_crm_web_app_qualifies_an_hour_after_approval():
    paths = ["apps/crm-web/src/App.vue", "apps/crm-web/src/views/Leads.vue"]

    merges_at = objection_window(POLICY, "T1", paths, APPROVED_AT, [])

    assert merges_at == APPROVED_AT + timedelta(minutes=60)


def test_a_pr_also_touching_the_orchestrator_doesnt_qualify():
    paths = ["apps/crm-web/src/App.vue", "orchestrator/sdlc/main.py"]

    assert objection_window(POLICY, "T1", paths, APPROVED_AT, []) is None


def test_t2_doesnt_qualify():
    assert objection_window(POLICY, "T2", ["apps/crm-web/src/App.vue"], APPROVED_AT, []) is None


def test_a_persons_hold_stops_the_window():
    comments = [_comment("Looks fine"), _comment("/HOLD until I've tried it")]

    assert objection_window(POLICY, "T1", ["apps/crm-web/a.vue"], APPROVED_AT, comments) is None


def test_a_bots_hold_doesnt_stop_the_window():
    comments = [_comment("/hold", kind="Bot")]

    merges_at = objection_window(POLICY, "T1", ["apps/crm-web/a.vue"], APPROVED_AT, comments)

    assert merges_at == APPROVED_AT + timedelta(minutes=60)


def test_a_hold_with_no_user_doesnt_stop_the_window():
    comments = [{"user": None, "body": "/hold"}]

    merges_at = objection_window(POLICY, "T1", ["apps/crm-web/a.vue"], APPROVED_AT, comments)

    assert merges_at == APPROVED_AT + timedelta(minutes=60)


def test_no_window_without_an_approval_changes_or_a_policy_window():
    assert objection_window(POLICY, "T1", ["apps/crm-web/a.vue"], None, []) is None
    assert objection_window(POLICY, "T1", [], APPROVED_AT, []) is None
    no_window = replace(POLICY, objection_window=None)
    assert objection_window(no_window, "T1", ["apps/crm-web/a.vue"], APPROVED_AT, []) is None


def test_window_signoff_fields_shape():
    pr = PullRequest(id=7, source="github", number=170, title="Tidy the leads table")
    approval = _reviewer_row("approve", row_id=42)

    fields = window_signoff_fields(pr, SHA, "T1", approval, POLICY)

    assert fields == {
        "agent": "objection_window",
        "agent_version": "v1",
        "subject_type": "pr",
        "subject_source": "github",
        "subject_id": 170,
        "trigger": "schedule",
        "head_sha": f"window-{SHA}"[:40],
        "tier": "T1",
        "output": {
            "commit": SHA,
            "reviewer_approval": 42,
            "approved_at": "2026-10-06T09:15:00",
            "minutes": 60,
            "paths": ["apps/crm-web/", "apps/insights-web/"],
        },
        "action_taken": {"signoff": "objection window passed with no /hold"},
    }
    assert len(fields["head_sha"]) == AgentDecision.__table__.c.head_sha.type.length == 40


def test_describe_window_appends_the_merge_time_to_a_pending_description():
    gate = evaluate(POLICY, "T1", ok=True, approvals=Approvals(), mode="enforce")

    text = describe_window(gate, datetime(2026, 10, 6, 10, 15))

    assert text == (
        "T1: waiting for human sign-off or, unless someone comments /hold, merges after 10:15 UTC"
    )


def test_describe_window_shows_the_time_in_utc():
    gate = evaluate(POLICY, "T1", ok=True, approvals=Approvals(), mode="enforce")
    local = datetime(2026, 10, 6, 12, 15, tzinfo=timezone(timedelta(hours=2)))

    assert describe_window(gate, local).endswith("merges after 10:15 UTC")
    assert describe_window(gate, local.astimezone(UTC)).endswith("merges after 10:15 UTC")


def test_describe_window_leaves_other_descriptions_alone_and_cuts_to_140():
    met = evaluate(POLICY, "T1", ok=True, approvals=Approvals(signoff=True), mode="enforce")
    assert describe_window(met, APPROVED_AT) == met.description

    shadow = evaluate(POLICY, "T3", ok=True, approvals=Approvals(), mode="shadow")
    assert len(describe_window(shadow, APPROVED_AT)) == 140
