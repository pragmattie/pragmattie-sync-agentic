from dataclasses import replace

import pytest

from sdlc.agents.gate import (
    DESCRIPTION_LIMIT,
    Approvals,
    evaluate,
    missing_for,
    requirements,
)
from sdlc.tiers import load_policy

POLICY = load_policy()

APPROVALS = {
    "nothing": Approvals(),
    "signoff": Approvals(signoff=True),
    "signoff_qa": Approvals(signoff=True, qa_done=True),
    "all": Approvals(signoff=True, qa_done=True, simulated_approved=True, ai_review=True),
}
SIGNOFF, QA, SECOND = "human sign-off", "manual QA", "simulated second approval"
EXPECTED_MISSING = {
    ("T0", "nothing"): [],
    ("T0", "signoff"): [],
    ("T0", "signoff_qa"): [],
    ("T0", "all"): [],
    ("T1", "nothing"): [SIGNOFF],
    ("T1", "signoff"): [],
    ("T1", "signoff_qa"): [],
    ("T1", "all"): [],
    ("T2", "nothing"): [SIGNOFF],
    ("T2", "signoff"): [],
    ("T2", "signoff_qa"): [],
    ("T2", "all"): [],
    ("T3", "nothing"): [SIGNOFF, QA, SECOND],
    ("T3", "signoff"): [QA, SECOND],
    ("T3", "signoff_qa"): [SECOND],
    ("T3", "all"): [],
}


@pytest.mark.parametrize("mode", ["shadow", "enforce"])
@pytest.mark.parametrize(("tier", "approvals"), list(EXPECTED_MISSING))
def test_evaluate_every_tier_approval_and_mode(tier, approvals, mode):
    missing = EXPECTED_MISSING[(tier, approvals)]

    gate = evaluate(POLICY, tier, ok=True, approvals=APPROVALS[approvals], mode=mode)

    would_be = "pending" if missing else "success"
    note = f"{tier}: waiting for {', '.join(missing)}" if missing else f"{tier}: requirements met"
    assert gate.would_be == would_be
    assert gate.missing == tuple(missing)
    if mode == "shadow":
        assert gate.state == "success"
        assert gate.description == f"Shadow mode (would be {would_be}). {note}"
    else:
        assert gate.state == would_be
        assert gate.description == note


@pytest.mark.parametrize(
    ("needs_ai_review", "ai_review", "state", "missing"),
    [
        (False, False, "success", ()),
        (True, False, "pending", ("AI review approval",)),
        (True, True, "success", ()),
    ],
)
def test_t0_waits_for_the_ai_reviewer_only_when_asked(needs_ai_review, ai_review, state, missing):
    gate = evaluate(
        POLICY,
        "T0",
        ok=True,
        approvals=Approvals(ai_review=ai_review),
        mode="enforce",
        needs_ai_review=needs_ai_review,
    )

    assert gate.state == state
    assert gate.missing == missing


def test_ai_review_never_stands_in_for_a_tier_that_needs_signoff():
    assert missing_for(POLICY, "T1", Approvals(), needs_ai_review=True) == [SIGNOFF]
    assert missing_for(POLICY, "T1", Approvals(ai_review=True), needs_ai_review=True) == [SIGNOFF]


def test_requirements_follow_the_policy():
    assert requirements(POLICY, "T0") == {
        "signoff": False,
        "simulated_second": False,
        "manual_qa": False,
    }
    assert requirements(POLICY, "T3") == {
        "signoff": True,
        "simulated_second": True,
        "manual_qa": True,
    }


def test_manual_qa_is_matched_whatever_its_case():
    tiers = dict(POLICY.tiers)
    tiers["T1"] = replace(tiers["T1"], tests="selected suites + MANUAL qa")
    policy = replace(POLICY, tiers=tiers)

    assert requirements(policy, "T1")["manual_qa"] is True


@pytest.mark.parametrize("approvals", list(APPROVALS))
def test_a_failed_risk_agent_fails_closed_in_enforce_mode(approvals):
    gate = evaluate(POLICY, "T2", ok=False, approvals=APPROVALS[approvals], mode="enforce")

    assert gate.state == "failure"
    assert gate.would_be == "failure"
    assert gate.missing == ("a successful risk assessment",)
    assert gate.description == "T2 fallback: the risk agent failed, so this fails closed"


def test_a_failed_risk_agent_passes_in_shadow_mode_saying_it_would_fail():
    gate = evaluate(POLICY, "T3", ok=False, approvals=Approvals(), mode="shadow")

    assert gate.state == "success"
    assert gate.would_be == "failure"
    assert gate.missing == ("a successful risk assessment",)
    assert gate.description == (
        "Shadow mode (would be failure). T3 fallback: the risk agent failed, so this fails closed"
    )


def test_descriptions_are_cut_to_the_status_limit():
    tier = "T3-with-a-name-long-enough-to-need-the-cut-here"
    policy = replace(POLICY, tiers={**POLICY.tiers, tier: POLICY.tiers["T3"]})

    gate = evaluate(policy, tier, ok=True, approvals=Approvals(), mode="shadow")

    full = (
        f"Shadow mode (would be pending). {tier}: waiting for "
        "human sign-off, manual QA, simulated second approval"
    )
    assert DESCRIPTION_LIMIT == 140
    assert len(full) > DESCRIPTION_LIMIT
    assert gate.description == full[:DESCRIPTION_LIMIT]
