"""Turn a risk score into a tier: the score's band, raised by floors and capped by caps.

A cap applies only when no floor matched, so a floor always beats a cap. Every assignment
carries plain-English reasons. No Claude call is involved.
"""

from dataclasses import dataclass

from sdlc.tables import PullRequest
from sdlc.tiers import TIER_IDS, Policy, Rule


@dataclass(frozen=True)
class Facts:
    module: str | None
    touches_migration: bool
    docs_only: bool
    touches_governance: bool


@dataclass(frozen=True)
class Assignment:
    tier: str
    score_tier: str | None  # None: there was no score
    floors: tuple[str, ...]
    capped_by: str | None
    reasons: tuple[str, ...]


def facts_of(pr: PullRequest) -> Facts:
    return Facts(
        module=pr.module,
        touches_migration=bool(pr.touches_migration),
        docs_only=bool(pr.docs_only),
        touches_governance=bool(pr.touches_governance),
    )


def _rank(tier: str) -> int:
    return TIER_IDS.index(tier)


def _matches(rule: Rule, facts: Facts) -> bool:
    for key, value in rule.when.items():
        if key == "modules":
            if facts.module not in value:
                return False
        elif getattr(facts, key) != value:
            return False
    return True


def matching(rules: tuple[Rule, ...], facts: Facts) -> list[Rule]:
    return [rule for rule in rules if _matches(rule, facts)]


def _floor_reason(rule: Rule) -> str:
    return f"Floor '{rule.name}' sets a minimum of {rule.tier}."


def band_for(policy: Policy, score: int) -> str:
    """The highest band whose minimum the score reaches."""
    tier = policy.bands[0][1]
    for minimum, band in policy.bands:
        if score >= minimum:
            tier = band
    return tier


def assign_tier(policy: Policy, score: int, facts: Facts) -> Assignment:
    score_tier = band_for(policy, score)
    tier = score_tier
    reasons = [f"Risk score {score} is in the {score_tier} band."]
    floors = matching(policy.floors, facts)
    for floor in floors:
        reasons.append(_floor_reason(floor))
        if _rank(floor.tier) > _rank(tier):
            tier = floor.tier
    capped_by = None
    if not floors:
        caps = matching(policy.caps, facts)
        if caps:
            cap = min(caps, key=lambda rule: _rank(rule.tier))
            if _rank(cap.tier) < _rank(tier):
                tier = cap.tier
                capped_by = cap.name
                reasons.append(f"Cap '{cap.name}' limits it to {cap.tier}.")
    return Assignment(
        tier=tier,
        score_tier=score_tier,
        floors=tuple(floor.name for floor in floors),
        capped_by=capped_by,
        reasons=tuple(reasons),
    )


def fallback_assignment(policy: Policy, facts: Facts, why: str) -> Assignment:
    """No score: the highest matching floor, else the policy's fallback tier."""
    floors = matching(policy.floors, facts)
    tier = max((floor.tier for floor in floors), key=_rank, default=policy.fallback_tier)
    return Assignment(
        tier=tier,
        score_tier=None,
        floors=tuple(floor.name for floor in floors),
        capped_by=None,
        reasons=(f"{why} Falling back to {tier}.",),
    )
