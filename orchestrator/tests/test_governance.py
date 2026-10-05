from dataclasses import replace

import pytest

from sdlc.governance import Facts, assign_tier, band_for, facts_of, fallback_assignment
from sdlc.tables import PullRequest
from sdlc.tiers import Rule, load_policy

PLAIN = Facts(module="leads", touches_migration=False, docs_only=False, touches_governance=False)


@pytest.fixture(scope="module")
def policy():
    return load_policy()


@pytest.mark.parametrize(
    ("score", "tier"),
    [
        (0, "T0"),
        (19, "T0"),
        (20, "T1"),
        (49, "T1"),
        (50, "T2"),
        (79, "T2"),
        (80, "T3"),
        (100, "T3"),
    ],
)
def test_band_for(policy, score, tier):
    assert band_for(policy, score) == tier


def test_score_alone_gives_its_band(policy):
    assignment = assign_tier(policy, 37, PLAIN)
    assert assignment.tier == "T1"
    assert assignment.score_tier == "T1"
    assert assignment.floors == ()
    assert assignment.capped_by is None
    assert assignment.reasons == ("Risk score 37 is in the T1 band.",)


def test_billing_auth_at_score_10_is_t3_with_both_reasons(policy):
    assignment = assign_tier(policy, 10, replace(PLAIN, module="billing_auth"))
    assert assignment.tier == "T3"
    assert assignment.score_tier == "T0"
    assert assignment.floors == ("billing_auth",)
    assert assignment.reasons == (
        "Risk score 10 is in the T0 band.",
        "Floor 'billing_auth' sets a minimum of T3.",
    )


def test_docs_only_at_score_30_is_capped_to_t0(policy):
    assignment = assign_tier(policy, 30, replace(PLAIN, docs_only=True))
    assert assignment.tier == "T0"
    assert assignment.score_tier == "T1"
    assert assignment.capped_by == "docs_or_config_only"
    assert assignment.reasons == (
        "Risk score 30 is in the T1 band.",
        "Cap 'docs_or_config_only' limits it to T0.",
    )


def test_docs_only_migration_stays_t3_because_the_floor_beats_the_cap(policy):
    assignment = assign_tier(policy, 30, replace(PLAIN, docs_only=True, touches_migration=True))
    assert assignment.tier == "T3"
    assert assignment.floors == ("schema_migration",)
    assert assignment.capped_by is None
    assert "Floor 'schema_migration' sets a minimum of T3." in assignment.reasons


def test_governance_file_change_at_score_5_is_t2(policy):
    assignment = assign_tier(policy, 5, replace(PLAIN, touches_governance=True))
    assert assignment.tier == "T2"
    assert assignment.floors == ("governance_files",)


def test_a_floor_never_lowers_a_higher_band(policy):
    assignment = assign_tier(policy, 90, replace(PLAIN, module="pipeline"))
    assert assignment.tier == "T3"
    assert assignment.floors == ("pipeline_or_forecasting",)


def test_two_floors_pick_the_highest(policy):
    facts = replace(PLAIN, module="forecasting", touches_migration=True, touches_governance=True)
    assignment = assign_tier(policy, 0, facts)
    assert assignment.tier == "T3"
    assert set(assignment.floors) == {
        "schema_migration",
        "pipeline_or_forecasting",
        "governance_files",
    }
    assert len(assignment.reasons) == 4


def test_two_floors_pick_the_highest_whatever_their_order(policy):
    floors = (
        Rule(name="high", tier="T3", when={"touches_governance": True}),
        Rule(name="low", tier="T1", when={"modules": ["leads"]}),
    )
    for order in (floors, floors[::-1]):
        assignment = assign_tier(
            replace(policy, floors=order), 0, replace(PLAIN, touches_governance=True)
        )
        assert assignment.tier == "T3"


def test_a_rule_needs_every_when_key_to_match(policy):
    floor = Rule(name="both", tier="T3", when={"modules": ["leads"], "docs_only": False})
    custom = replace(policy, floors=(floor,), caps=())
    assert assign_tier(custom, 0, PLAIN).tier == "T3"
    assert assign_tier(custom, 0, replace(PLAIN, docs_only=True)).tier == "T0"
    assert assign_tier(custom, 0, replace(PLAIN, module="accounts")).tier == "T0"


def test_fallback_without_a_floor_is_the_fallback_tier(policy):
    assignment = fallback_assignment(policy, PLAIN, "The risk agent failed.")
    assert assignment.tier == "T2"
    assert assignment.score_tier is None
    assert assignment.floors == ()
    assert assignment.reasons == ("The risk agent failed. Falling back to T2.",)


def test_fallback_on_billing_auth_is_t3(policy):
    assignment = fallback_assignment(
        policy, replace(PLAIN, module="billing_auth"), "The risk agent failed."
    )
    assert assignment.tier == "T3"
    assert assignment.floors == ("billing_auth",)
    assert assignment.reasons == ("The risk agent failed. Falling back to T3.",)


def test_facts_of_reads_the_pull_request():
    pr = PullRequest(
        module="billing_auth", touches_migration=True, docs_only=False, touches_governance=True
    )
    assert facts_of(pr) == Facts(
        module="billing_auth", touches_migration=True, docs_only=False, touches_governance=True
    )
