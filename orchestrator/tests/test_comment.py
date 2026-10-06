from datetime import datetime

import pytest

from sdlc.agents.comment import (
    MARKER,
    NEEDS_HEADING,
    OVERRIDES_END,
    OVERRIDES_START,
    QA_TEXT,
    SIGNOFF_TEXT,
    Meta,
    comment_head,
    heading,
    needs_lines,
    overrides_block,
    refresh,
    render,
    retier,
)
from sdlc.agents.gate import Approvals, evaluate
from sdlc.agents.llm import LLMResult
from sdlc.agents.pr_risk import Assessment
from sdlc.agents.signoff import ticked
from sdlc.governance import Assignment
from sdlc.scoring import MAX_POINTS, Features
from sdlc.tiers import load_policy

POLICY = load_policy()
SHA, OTHER_SHA = "c" * 40, "d" * 40
APPROVER = "Sim Approver"
SIGNALS = {name: 0 for name in MAX_POINTS} | {"change_size": 10, "schema_migration": 15}
FEATURES = Features(
    lines=300,
    files_changed=4,
    modules_touched=1,
    touches_migration=False,
    docs_only=False,
    test_files_changed=1,
    review_count=0,
    rework_commits=0,
    real_ci_failures=0,
    at=datetime(2026, 10, 6, 9, 0),
    module_rate=0.1,
    max_module_rate=0.2,
    author_ratio=None,
)
SIGNAL_TABLE = (
    "<details><summary>Signal points</summary>\n\n"
    "| Signal | Points |\n"
    "| --- | --- |\n"
    "| change_size | 10/20 |\n"
    "| blast_radius | 0/10 |\n"
    "| module_risk | 0/20 |\n"
    "| schema_migration | 15/15 |\n"
    "| author_record | 0/10 |\n"
    "| tests_with_change | 0/10 |\n"
    "| ci_signal | 0/10 |\n"
    "| review_depth | 0/5 |\n"
    "| timing | 0/5 |\n"
    "| rework_churn | 0/5 |\n\n"
    "</details>"
)


def _llm(model="claude-sonnet-5"):
    return LLMResult(
        data={"adjustment": 99, "justification": "audit copy", "top_reasons": [], "test_gaps": []},
        model=model,
        input_tokens=1200,
        output_tokens=300,
        cache_read_tokens=0,
        cache_write_tokens=0,
        latency_ms=900,
        request_id="req_1",
        cost_usd=0.01,
    )


def _assessment(tier, *, raw=25, adjustment=0, clamped=False, status="ok", llm=True, **answer):
    ok = status == "ok"
    reasons = (f"Risk score {raw + adjustment} is in the {tier} band.",)
    if not ok:
        reasons = (f"The risk agent failed ({status}). Falling back to {tier}.",)
    return Assessment(
        raw_score=raw,
        signals=SIGNALS,
        adjustment=adjustment if ok else None,
        clamped=clamped,
        final_score=raw + (adjustment if ok else 0),
        assignment=Assignment(
            tier=tier, score_tier=tier if ok else None, floors=(), capped_by=None, reasons=reasons
        ),
        status=status,
        error=None if ok else "boom",
        justification=answer.get("justification") if ok else None,
        top_reasons=answer.get("top_reasons", []) if ok else [],
        test_gaps=answer.get("test_gaps", []) if ok else [],
        llm=_llm() if llm else None,
        features=FEATURES,
        model="claude-sonnet-5",
    )


def _meta(mode="enforce", decision_id=42):
    return Meta(decision_id=decision_id, mode=mode, approver_name=APPROVER, head_sha=SHA)


def _render(assessment, approvals=None, mode="enforce", decision_id=42, **kwargs):
    approvals = approvals or Approvals()
    tier = kwargs.get("tier") or assessment.assignment.tier
    gate = evaluate(POLICY, tier, ok=assessment.ok, approvals=approvals, mode=mode)
    return render(assessment, gate, approvals, POLICY, _meta(mode, decision_id), **kwargs)


# render


def test_render_a_t0_in_enforce_mode_needs_nothing():
    assessment = _assessment(
        "T0",
        raw=12,
        adjustment=-2,
        justification="A copy change only.",
        top_reasons=["Changes one label.", "No logic touched.", "Tests unchanged."],
    )

    assert _render(assessment) == (
        f"{MARKER}\n<!-- head:{SHA} -->\n## Risk review: T0 (Auto)\n\n"
        "**Score 10/100**: rubric 12, model adjustment -2\n\n"
        "**Why**\n1. Changes one label.\n2. No logic touched.\n3. Tests unchanged.\n\n"
        "_A copy change only._\n\n"
        "- Risk score 10 is in the T0 band.\n\n"
        f"{OVERRIDES_START}\n{OVERRIDES_END}\n\n"
        f"{NEEDS_HEADING}\n- Nothing. A person doesn't need to act.\n\n"
        f"{SIGNAL_TABLE}\n\n"
        "<sub>decision 42 · claude-sonnet-5 · 1500 tokens · PragMattie Sync demo</sub>\n"
    )


def test_render_a_t1_in_shadow_mode_shows_the_shadow_line_and_the_signoff_box():
    assessment = _assessment(
        "T1", raw=30, adjustment=5, top_reasons=["Edits the lead form."], test_gaps=["Empty name"]
    )

    assert _render(assessment, mode="shadow") == (
        f"{MARKER}\n<!-- head:{SHA} -->\n## Risk review: T1 (Light)\n\n"
        "> **Shadow mode.** This check always passes for now. "
        "It shows what enforce mode would do: **pending**.\n\n"
        "**Score 35/100**: rubric 30, model adjustment +5\n\n"
        "**Why**\n1. Edits the lead form.\n\n"
        "**Tests that look missing**\n- Empty name\n\n"
        "- Risk score 35 is in the T1 band.\n\n"
        f"{OVERRIDES_START}\n{OVERRIDES_END}\n\n"
        f"{NEEDS_HEADING}\n- [ ] {SIGNOFF_TEXT}\n"
        "  (A person's GitHub approval of this commit counts too.)\n\n"
        f"{SIGNAL_TABLE}\n\n"
        "<sub>decision 42 · claude-sonnet-5 · 1500 tokens · PragMattie Sync demo</sub>\n"
    )


def test_render_a_clamped_t3_has_both_boxes_and_waits_for_the_simulated_approval():
    assessment = _assessment("T3", raw=70, adjustment=15, clamped=True, top_reasons=["Auth."])

    assert _render(assessment, Approvals(signoff=True)) == (
        f"{MARKER}\n<!-- head:{SHA} -->\n## Risk review: T3 (Critical)\n\n"
        "**Score 85/100**: rubric 70, model adjustment +15 (limited to the +/-15 maximum)\n\n"
        "**Why**\n1. Auth.\n\n"
        "- Risk score 85 is in the T3 band.\n\n"
        f"{OVERRIDES_START}\n{OVERRIDES_END}\n\n"
        f"{NEEDS_HEADING}\n- [x] {SIGNOFF_TEXT}\n"
        "  (A person's GitHub approval of this commit counts too.)\n"
        f"- [ ] {QA_TEXT}\n"
        "- Simulated second approval: **waiting** "
        f"({APPROVER}, controlled by the repo owner; not a real second person)\n\n"
        f"{SIGNAL_TABLE}\n\n"
        "<sub>decision 42 · claude-sonnet-5 · 1500 tokens · PragMattie Sync demo</sub>\n"
    )


def test_render_a_failed_assessment_says_it_fails_closed():
    assessment = _assessment("T2", status="timeout", llm=False)

    assert _render(assessment, decision_id=None) == (
        f"{MARKER}\n<!-- head:{SHA} -->\n## Risk review: T2 (Standard)\n\n"
        "**The risk agent could not score this PR** (timeout). It is treated as T2 and the "
        "check fails closed. It will try again shortly.\n\n"
        "- The risk agent failed (timeout). Falling back to T2.\n\n"
        f"{OVERRIDES_START}\n{OVERRIDES_END}\n\n"
        f"{NEEDS_HEADING}\n- [ ] {SIGNOFF_TEXT}\n"
        "  (A person's GitHub approval of this commit counts too.)\n\n"
        f"{SIGNAL_TABLE}\n\n"
        "<sub>decision not recorded · PragMattie Sync demo</sub>\n"
    )


def test_render_reads_the_answer_from_the_assessment_not_the_raw_model_output():
    body = _render(_assessment("T1", justification="Stored justification."))

    assert "Stored justification." in body
    assert "audit copy" not in body


def test_render_places_the_tests_section_and_override_lines():
    body = _render(_assessment("T1"), tests="**Tests to run**\n- api", override_lines=["- raised"])

    assert "**Tests to run**\n- api\n\n<!-- overrides -->\n**Tier overrides**" in body
    assert "- raised\n<!-- /overrides -->\n\n**What this tier needs**" in body


def test_comment_head_reads_the_commit():
    assert comment_head(_render(_assessment("T0"))) == SHA
    assert comment_head("no marker here") is None
    assert comment_head(None) is None


# heading, needs and overrides


@pytest.mark.parametrize(
    ("tier", "agent_tier", "expected"),
    [
        ("T1", "T1", "## Risk review: T1 (Light)"),
        ("T3", "T1", "## Risk review: T3 (Critical), raised from T1 by a person"),
        ("T1", "T2", "## Risk review: T1 (Light), lowered from T2 by a person"),
    ],
)
def test_heading(tier, agent_tier, expected):
    assert heading(POLICY, tier, agent_tier) == expected


def test_needs_lines_tick_what_is_done():
    lines = needs_lines(POLICY, "T3", Approvals(qa_done=True, simulated_approved=True), APPROVER)

    assert lines[1] == f"- [ ] {SIGNOFF_TEXT}"
    assert lines[3] == f"- [x] {QA_TEXT}"
    assert lines[4].startswith("- Simulated second approval: **approved** (Sim Approver,")


def test_overrides_block_is_empty_without_lines():
    assert overrides_block(None) == f"{OVERRIDES_START}\n{OVERRIDES_END}"
    assert overrides_block(["- a"]).splitlines()[1].startswith("**Tier overrides** (raise with")


# refresh and retier


def test_refresh_changes_only_the_live_parts():
    assessment = _assessment("T3", raw=85, top_reasons=["Auth."])
    body = _render(assessment, Approvals(signoff=True), mode="shadow")
    gate = evaluate(POLICY, "T3", ok=True, approvals=Approvals(signoff=True), mode="shadow")
    assert gate.would_be == "pending"
    done = evaluate(
        POLICY,
        "T3",
        ok=True,
        approvals=Approvals(signoff=True, qa_done=True, simulated_approved=True),
        mode="shadow",
    )

    refreshed = refresh(body, done, simulated_approved=True)

    expected = body.replace("would do: **pending**", "would do: **success**").replace(
        "approval: **waiting**", "approval: **approved**"
    )
    assert refreshed == expected
    assert refreshed != body


def test_retier_keeps_the_review_and_ticks_and_rewrites_the_tier_parts():
    assessment = _assessment("T1", raw=30, justification="Kept.", top_reasons=["Kept reason."])
    body = _render(assessment, Approvals(signoff=True))

    raised = retier(
        body,
        POLICY,
        tier="T3",
        agent_tier="T1",
        approvals=Approvals(),
        approver_name=APPROVER,
        override_lines=["- @pat raised this to T3."],
    )

    assert "## Risk review: T3 (Critical), raised from T1 by a person" in raised
    assert "**Score 30/100**" in raised and "_Kept._" in raised and "1. Kept reason." in raised
    assert f"- [x] {SIGNOFF_TEXT}" in raised
    assert f"- [ ] {QA_TEXT}" in raised
    assert "- Simulated second approval: **waiting**" in raised
    assert f"{OVERRIDES_START}\n**Tier overrides**" in raised
    assert "- @pat raised this to T3.\n" + OVERRIDES_END in raised
    assert raised.endswith(body[body.index("<details>") :])
    assert ticked(raised, SHA) == (True, False)


def test_retier_adds_the_overrides_block_to_an_older_comment():
    body = _render(_assessment("T2")).replace(f"{OVERRIDES_START}\n{OVERRIDES_END}\n\n", "")
    assert OVERRIDES_START not in body

    lowered = retier(
        body,
        POLICY,
        tier="T1",
        agent_tier="T2",
        approvals=Approvals(),
        approver_name=APPROVER,
        override_lines=["- @pat lowered this: copy only."],
    )

    assert lowered.count(OVERRIDES_START) == 1
    assert lowered.index(OVERRIDES_END) < lowered.index(NEEDS_HEADING)
    assert "## Risk review: T1 (Light), lowered from T2 by a person" in lowered


# Round trip with the gate's reader


def test_a_ticked_render_reads_back_as_ticked_for_the_same_commit_only():
    body = _render(_assessment("T3"), Approvals(signoff=True))

    assert ticked(body, SHA) == (True, False)
    assert ticked(body, OTHER_SHA) == (False, False)
    assert ticked(_render(_assessment("T3")), SHA) == (False, False)
