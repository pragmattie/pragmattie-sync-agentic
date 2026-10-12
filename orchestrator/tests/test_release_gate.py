from dataclasses import replace

import pytest

from sdlc.agents.release_gate import (
    DESCRIPTION_LIMIT,
    STATES,
    OpenIncident,
    ReleaseFacts,
    evaluate,
    output_of,
    release_tier,
)
from sdlc.tiers import load_policy

POLICY = load_policy()
PIPELINE_61 = OpenIncident("pipeline", 61, "Pipeline degraded")


def _facts(tier="T2", **changes) -> ReleaseFacts:
    return replace(
        ReleaseFacts(tier=tier, prs=(40, 41), modules=("pipeline",), ci="passed"), **changes
    )


def _enforce(facts):
    return evaluate(POLICY, facts, mode="enforce")


@pytest.mark.parametrize("tier", ["T0", "T1"])
def test_t0_and_t1_deploy_automatically_even_with_an_incident_open(tier):
    verdict = _enforce(_facts(tier, open_incidents=(PIPELINE_61,), ci="failed"))

    assert (verdict.verdict, verdict.state, verdict.would_be) == ("release", "success", "success")
    assert verdict.reasons == ()
    assert verdict.environment == "production"
    assert verdict.description == f"{tier} · Release: deploys automatically"


def test_t2_passes_its_checks():
    verdict = _enforce(_facts())

    assert (verdict.verdict, verdict.state) == ("release", "success")
    assert verdict.environment == "production"
    assert verdict.description == "T2 · Release: checks passed"


def test_an_open_incident_holds():
    verdict = _enforce(_facts(open_incidents=(PIPELINE_61,)))

    assert (verdict.verdict, verdict.state) == ("hold", "pending")
    assert verdict.reasons == ("open incident in pipeline (#61)",)
    assert verdict.description == "T2 · Held: open incident in pipeline (#61)"


def test_an_incident_without_a_number_names_only_its_module():
    verdict = _enforce(_facts(open_incidents=(OpenIncident("leads"),)))

    assert verdict.description == "T2 · Held: open incident in leads"


def test_ci_running_holds():
    verdict = _enforce(_facts(ci="running"))

    assert (verdict.verdict, verdict.state) == ("hold", "pending")
    assert verdict.description == "T2 · Held: waiting for CI on the release commit"


def test_ci_failed_blocks_and_beats_a_hold_keeping_both_reasons():
    verdict = _enforce(_facts(ci="failed", open_incidents=(PIPELINE_61,)))

    assert (verdict.verdict, verdict.state, verdict.would_be) == ("blocked", "failure", "failure")
    assert verdict.reasons == (
        "CI failed on the release commit",
        "open incident in pipeline (#61)",
    )
    assert verdict.description == (
        "T2 · Blocked: CI failed on the release commit; open incident in pipeline (#61)"
    )


def test_too_many_recent_failed_deploys_hold_with_the_exact_wording():
    verdict = _enforce(_facts(recent_deploys=5, failed_deploys=2))

    assert verdict.verdict == "hold"
    assert verdict.description == (
        "T2 · Held: 2 of 5 deploys in 7 days caused incidents (over 30%)"
    )


@pytest.mark.parametrize(("recent", "failed"), [(2, 2), (10, 3), (0, 0)])
def test_too_few_deploys_or_a_rate_at_the_limit_do_not_hold(recent, failed):
    verdict = _enforce(_facts(recent_deploys=recent, failed_deploys=failed))

    assert verdict.verdict == "release"


def test_reasons_keep_their_order():
    verdict = _enforce(
        _facts(
            "T3",
            ci="running",
            open_incidents=(PIPELINE_61, OpenIncident("leads")),
            recent_deploys=4,
            failed_deploys=2,
            signoff=False,
        )
    )

    assert verdict.reasons == (
        "waiting for CI on the release commit",
        "open incident in pipeline (#61)",
        "open incident in leads",
        "2 of 4 deploys in 7 days caused incidents (over 30%)",
        "waiting for sign-off (simulated)",
    )


def test_t3_without_simulated_signoff_asks_a_person_in_github():
    verdict = _enforce(_facts("T3"))

    assert (verdict.verdict, verdict.state) == ("release", "success")
    assert verdict.environment == "production-signoff"
    assert verdict.description == "T3 · Release: checks passed; approve the deployment in GitHub"


def test_t3_waiting_for_simulated_signoff_holds():
    verdict = _enforce(_facts("T3", signoff=False))

    assert (verdict.verdict, verdict.state) == ("hold", "pending")
    assert verdict.environment == "production-signoff"
    assert verdict.description == "T3 · Held: waiting for sign-off (simulated)"


def test_t3_signed_off_in_the_simulation_releases():
    verdict = _enforce(_facts("T3", signoff=True))

    assert verdict.verdict == "release"
    assert verdict.description == "T3 · Release: checks passed and signed off (simulated)"


def test_t2_ignores_signoff():
    assert _enforce(_facts(signoff=False)).verdict == "release"


@pytest.mark.parametrize(
    ("facts", "would_be", "text"),
    [
        (_facts(), "success", "Release: checks passed"),
        (_facts(ci="running"), "pending", "Held: waiting for CI on the release commit"),
        (_facts(ci="failed"), "failure", "Blocked: CI failed on the release commit"),
    ],
)
def test_shadow_always_succeeds_and_says_what_enforce_would_do(facts, would_be, text):
    verdict = evaluate(POLICY, facts, mode="shadow")

    assert verdict.state == "success"
    assert verdict.would_be == would_be
    assert verdict.verdict == {state: name for name, state in STATES.items()}[would_be]
    assert verdict.description == f"T2 · Shadow (would be {would_be}). {text}"


def test_descriptions_are_cut_to_the_limit_and_start_with_the_tier():
    incidents = tuple(OpenIncident(f"module-{i}", 1000 + i) for i in range(10))

    for mode in ("enforce", "shadow"):
        verdict = evaluate(POLICY, _facts(open_incidents=incidents), mode=mode)
        assert len(verdict.description) == DESCRIPTION_LIMIT
        assert verdict.description.startswith("T2 · ")
    assert len(verdict.reasons) == 10


def test_the_failure_rate_follows_the_policy():
    policy = replace(
        POLICY, release=replace(POLICY.release, failure_max=0.5, failure_window_days=14)
    )

    assert evaluate(policy, _facts(recent_deploys=4, failed_deploys=2), mode="enforce").verdict == (
        "release"
    )
    verdict = evaluate(policy, _facts(recent_deploys=5, failed_deploys=3), mode="enforce")
    assert verdict.reasons == ("3 of 5 deploys in 14 days caused incidents (over 50%)",)


@pytest.mark.parametrize(
    ("tiers", "expected"),
    [
        (["T0", "T2", "T1"], "T2"),
        (["T3", "T0"], "T3"),
        (["T0", "T1"], "T1"),
        (["T0", None], "T2"),
        (["T0", "T9"], "T2"),
        (["T3", None], "T3"),
        ([], "T2"),
    ],
)
def test_release_tier_is_the_highest_and_fails_closed(tiers, expected):
    assert release_tier(tiers, "T2") == expected


def test_output_holds_the_verdict_and_its_facts():
    facts = _facts("T3", open_incidents=(PIPELINE_61,), recent_deploys=3, failed_deploys=0)
    verdict = _enforce(facts)

    assert output_of(facts, verdict, release=7) == {
        "verdict": "hold",
        "would_be": "pending",
        "reasons": ["open incident in pipeline (#61)"],
        "environment": "production-signoff",
        "prs": [40, 41],
        "modules": ["pipeline"],
        "ci": "passed",
        "open_incidents": [{"module": "pipeline", "number": 61, "title": "Pipeline degraded"}],
        "recent_deploys": 3,
        "failed_deploys": 0,
        "signoff": None,
        "release": 7,
    }
