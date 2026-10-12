import copy
from pathlib import Path

import pytest
import yaml

from sdlc.tiers import (
    POLICY,
    RELEASE_DEFAULTS,
    TIER_IDS,
    Models,
    ObjectionWindow,
    PolicyError,
    ReleaseRules,
    Rule,
    Tier,
    load_policy,
)

# The release gate section waits in ci/proposed/ until a person applies it; read it from there
# while it exists, else from the live policy.
PROPOSED = Path(__file__).resolve().parents[2] / "ci" / "proposed" / "tiers.yaml"
RELEASE_POLICY = PROPOSED if PROPOSED.exists() else POLICY
V1_RELEASE = ReleaseRules(
    environment="production",
    signoff_environment="production-signoff",
    failure_window_days=7,
    failure_max=0.3,
    failure_min_deploys=3,
    synthetic_signoff_hours=4.0,
)

VALID = {
    "score_bands": [
        {"tier": "T0", "min": 0},
        {"tier": "T1", "min": 20},
        {"tier": "T2", "min": 50},
        {"tier": "T3", "min": 80},
    ],
    "floors": [{"name": "billing_auth", "tier": "T3", "when": {"modules": ["billing_auth"]}}],
    "caps": [{"name": "docs_or_config_only", "tier": "T0", "when": {"docs_only": True}}],
    "objection_window": {"tiers": ["T1"], "minutes": 60, "paths": ["apps/crm-web/"]},
    "fallback_tier": "T2",
    "tiers": {
        tier_id: {
            "name": tier_id,
            "humans": 1,
            "senior_human": False,
            "code_owner": False,
            "tests": "full suite",
            "deploy": "automatic",
            "agent_check": "passes_after_approvals",
            "release": release,
        }
        for tier_id, release in zip(
            TIER_IDS, ("automatic", "automatic", "checks", "signoff"), strict=True
        )
    },
    "models": {
        "build": "claude-opus-5-5",
        "review": "claude-opus-5-5",
        "label_overrides": {"model:opus": "claude-opus-5-5"},
    },
}


def _write(tmp_path, data) -> Path:
    path = tmp_path / "tiers.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return path


def test_committed_policy_values():
    policy = load_policy(POLICY)

    assert policy.bands == ((0, "T0"), (20, "T1"), (50, "T2"), (80, "T3"))
    assert policy.floors == (
        Rule("billing_auth", "T3", {"modules": ["billing_auth"]}),
        Rule("schema_migration", "T3", {"touches_migration": True}),
        Rule("pipeline_or_forecasting", "T2", {"modules": ["pipeline", "forecasting"]}),
        Rule("governance_files", "T2", {"touches_governance": True}),
    )
    assert policy.caps == (Rule("docs_or_config_only", "T0", {"docs_only": True}),)
    assert policy.objection_window == ObjectionWindow(
        tiers=("T1",), minutes=60, paths=("apps/crm-web/", "apps/insights-web/")
    )
    assert policy.fallback_tier == "T2"
    assert policy.tiers == {
        "T0": Tier(
            "T0", "Auto", 0, False, False, "selected suites", "automatic", "passes_alone",
            "automatic",
        ),
        "T1": Tier(
            "T1", "Light", 1, False, False, "selected suites", "automatic after merge",
            "passes_after_approvals", "automatic",
        ),
        "T2": Tier(
            "T2", "Standard", 1, True, False, "full suite", "release gate checks",
            "passes_after_approvals", "checks",
        ),
        "T3": Tier(
            "T3", "Critical", 2, True, True, "full suite + manual QA", "human sign-off required",
            "reports_only", "signoff",
        ),
    }  # fmt: skip
    assert policy.models == Models(
        build="claude-opus-5-5",
        review="claude-opus-5-5",
        label_overrides={"model:sonnet": "claude-sonnet-5", "model:opus": "claude-opus-5-5"},
    )


def test_committed_window_excludes_t2_t3():
    window = load_policy(POLICY).objection_window

    assert window is not None
    assert not {"T2", "T3"} & set(window.tiers)


def test_valid_fixture_loads(tmp_path):
    assert load_policy(_write(tmp_path, VALID)).fallback_tier == "T2"


def test_absent_objection_window_is_none(tmp_path):
    data = copy.deepcopy(VALID)
    del data["objection_window"]

    assert load_policy(_write(tmp_path, data)).objection_window is None


def _set(*keys, value):
    def change(data):
        target = data
        for key in keys[:-1]:
            target = target[key]
        target[keys[-1]] = value

    return change


def _delete(*keys):
    def change(data):
        target = data
        for key in keys[:-1]:
            target = target[key]
        del target[keys[-1]]

    return change


def _swap_bands(data):
    bands = data["score_bands"]
    bands[1]["tier"], bands[2]["tier"] = bands[2]["tier"], bands[1]["tier"]


def _extra_tier(data):
    data["tiers"]["T4"] = copy.deepcopy(data["tiers"]["T3"])


REFUSALS = {
    # A tier id is unknown anywhere.
    "unknown band tier": ("score_bands", _set("score_bands", 1, "tier", value="T9")),
    "unknown floor tier": ("floors", _set("floors", 0, "tier", value="T9")),
    "unknown cap tier": ("caps", _set("caps", 0, "tier", value="T9")),
    "unknown fallback tier": ("fallback_tier", _set("fallback_tier", value="T9")),
    "unknown tiers key": ("tiers", _extra_tier),
    "unknown window tier": ("objection_window", _set("objection_window", "tiers", value=["T9"])),
    # Band minimums.
    "bands not starting at 0": ("score_bands", _set("score_bands", 0, "min", value=5)),
    "bands not rising": ("score_bands", _set("score_bands", 2, "min", value=20)),
    "bands over 100": ("score_bands", _set("score_bands", 3, "min", value=101)),
    "bands out of order": ("score_bands", _swap_bands),
    "band missing": ("score_bands", _delete("score_bands", 3)),
    # Tiers.
    "tier missing": ("tiers", _delete("tiers", "T2")),
    "humans negative": ("tiers", _set("tiers", "T1", "humans", value=-1)),
    "humans fractional": ("tiers", _set("tiers", "T1", "humans", value=1.5)),
    "humans bool": ("tiers", _set("tiers", "T1", "humans", value=True)),
    "agent_check unknown": ("tiers", _set("tiers", "T0", "agent_check", value="maybe")),
    "release unknown": ("tiers", _set("tiers", "T0", "release", value="whenever")),
    "release asks less higher up": ("tiers", _set("tiers", "T3", "release", value="automatic")),
    # Floors and caps.
    "floor when empty": ("floors", _set("floors", 0, "when", value={})),
    "floor when unknown key": ("floors", _set("floors", 0, "when", value={"size": "big"})),
    "cap when empty": ("caps", _set("caps", 0, "when", value={})),
    "cap when unknown key": ("caps", _set("caps", 0, "when", value={"size": "big"})),
    # Objection window.
    "window without tiers": ("objection_window", _delete("objection_window", "tiers")),
    "window without paths": ("objection_window", _delete("objection_window", "paths")),
    "window without minutes": ("objection_window", _delete("objection_window", "minutes")),
    "window minutes below 1": ("objection_window", _set("objection_window", "minutes", value=0)),
    "window lists T2": ("objection_window", _set("objection_window", "tiers", value=["T1", "T2"])),
    "window lists T3": ("objection_window", _set("objection_window", "tiers", value=["T3"])),
    # Models.
    "build empty": ("models", _set("models", "build", value="")),
    "review empty": ("models", _set("models", "review", value=" ")),
    "override key without prefix": (
        "models",
        _set("models", "label_overrides", value={"sonnet": "claude-sonnet-5"}),
    ),
}


@pytest.mark.parametrize(("section", "change"), REFUSALS.values(), ids=REFUSALS.keys())
def test_bad_policy_is_refused_naming_its_section(tmp_path, section, change):
    data = copy.deepcopy(VALID)
    change(data)

    with pytest.raises(PolicyError, match=rf"^{section}:"):
        load_policy(_write(tmp_path, data))


def test_missing_file_is_refused(tmp_path):
    with pytest.raises(PolicyError, match=r"^policy: "):
        load_policy(tmp_path / "absent.yaml")


def test_unparseable_yaml_is_refused(tmp_path):
    path = tmp_path / "tiers.yaml"
    path.write_text("score_bands: [unclosed\n", encoding="utf-8")

    with pytest.raises(PolicyError, match=r"^policy: "):
        load_policy(path)


def test_release_defaults_are_v1_values_when_the_section_is_absent(tmp_path):
    assert ReleaseRules(**RELEASE_DEFAULTS) == V1_RELEASE
    assert load_policy(_write(tmp_path, VALID)).release == V1_RELEASE


def test_the_release_gate_section_loads_the_same_values():
    policy = load_policy(RELEASE_POLICY)

    assert policy.release == V1_RELEASE
    assert [policy.tiers[tier].release for tier in TIER_IDS] == [
        "automatic", "automatic", "checks", "signoff",
    ]  # fmt: skip


def test_the_release_gate_section_overrides_the_defaults(tmp_path):
    data = copy.deepcopy(VALID)
    data["release_gate"] = {
        "environment": "prod",
        "signoff_environment": "prod-approval",
        "failure_rate": {"window_days": 14, "max": 0.5, "min_deploys": 5},
        "synthetic_signoff_hours": 8,
    }

    assert load_policy(_write(tmp_path, data)).release == ReleaseRules(
        "prod", "prod-approval", 14, 0.5, 5, 8.0
    )


MISSING_OR_INVALID = r"^release_gate: missing or invalid setting \("
SAME_ENVIRONMENT = r"^release_gate: the sign-off environment must be a separate environment$"
BAD_FAILURE_RATE = r"^release_gate: failure_rate needs window_days >= 1 and max from 0 to 1$"
RELEASE_REFUSALS = {
    "section not a mapping": ("production", MISSING_OR_INVALID),
    "failure_rate not a mapping": ({"failure_rate": [7]}, MISSING_OR_INVALID),
    "environment empty": ({"environment": ""}, MISSING_OR_INVALID),
    "signoff environment not text": ({"signoff_environment": 3}, MISSING_OR_INVALID),
    "window_days fractional": ({"failure_rate": {"window_days": 1.5}}, MISSING_OR_INVALID),
    "min_deploys text": ({"failure_rate": {"min_deploys": "three"}}, MISSING_OR_INVALID),
    "max text": ({"failure_rate": {"max": "a third"}}, MISSING_OR_INVALID),
    "signoff hours bool": ({"synthetic_signoff_hours": True}, MISSING_OR_INVALID),
    "signoff hours negative": ({"synthetic_signoff_hours": -1}, MISSING_OR_INVALID),
    "same environment": ({"signoff_environment": "production"}, SAME_ENVIRONMENT),
    "window_days below 1": ({"failure_rate": {"window_days": 0}}, BAD_FAILURE_RATE),
    "max below 0": ({"failure_rate": {"max": -0.1}}, BAD_FAILURE_RATE),
    "max above 1": ({"failure_rate": {"max": 1.5}}, BAD_FAILURE_RATE),
}


@pytest.mark.parametrize(("section", "message"), RELEASE_REFUSALS.values(), ids=RELEASE_REFUSALS)
def test_bad_release_gate_settings_are_refused(tmp_path, section, message):
    data = copy.deepcopy(VALID)
    data["release_gate"] = section

    with pytest.raises(PolicyError, match=message):
        load_policy(_write(tmp_path, data))
