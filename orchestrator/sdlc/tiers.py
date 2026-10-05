"""Load and check the governance policy: tiers, score bands, floors, caps and models.

``load_policy()`` reads ``policies/tiers.yaml`` and raises ``PolicyError`` naming the section at
the first thing wrong with it, so no pull request is ever gated on a broken policy.
"""

from dataclasses import dataclass
from pathlib import Path

import yaml

TIER_IDS = ("T0", "T1", "T2", "T3")
POLICY = Path(__file__).resolve().parent.parent / "policies" / "tiers.yaml"

AGENT_CHECKS = ("passes_alone", "passes_after_approvals", "reports_only")
RELEASES = ("automatic", "checks", "signoff")  # lowest demand to highest
WHEN_KEYS = ("modules", "touches_migration", "docs_only", "touches_governance")
NEVER_IN_WINDOW = ("T2", "T3")


class PolicyError(ValueError):
    """The policy file is invalid; the message starts with the section at fault."""


@dataclass(frozen=True)
class Tier:
    id: str
    name: str
    humans: int
    senior_human: bool
    code_owner: bool
    tests: str
    deploy: str
    agent_check: str
    release: str


@dataclass(frozen=True)
class Rule:
    name: str
    tier: str
    when: dict


@dataclass(frozen=True)
class ObjectionWindow:
    tiers: tuple[str, ...]
    minutes: int
    paths: tuple[str, ...]


@dataclass(frozen=True)
class Models:
    build: str
    review: str
    label_overrides: dict[str, str]


@dataclass(frozen=True)
class Policy:
    bands: tuple[tuple[int, str], ...]
    floors: tuple[Rule, ...]
    caps: tuple[Rule, ...]
    fallback_tier: str
    tiers: dict[str, Tier]
    objection_window: ObjectionWindow | None
    models: Models


def load_policy(path: Path = POLICY) -> Policy:
    with open(path, encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise PolicyError("policy: the file must be a mapping of sections")
    return Policy(
        bands=_bands(data.get("score_bands")),
        floors=_rules("floors", data.get("floors")),
        caps=_rules("caps", data.get("caps")),
        fallback_tier=_tier_id("fallback_tier", data.get("fallback_tier")),
        tiers=_tiers(data.get("tiers")),
        objection_window=_window(data.get("objection_window")),
        models=_models(data.get("models")),
    )


def _tier_id(section: str, value) -> str:
    if value not in TIER_IDS:
        raise PolicyError(f"{section}: unknown tier id {value!r}; expected one of {TIER_IDS}")
    return value


def _is_whole(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _bands(raw) -> tuple[tuple[int, str], ...]:
    if not isinstance(raw, list) or not all(isinstance(band, dict) for band in raw):
        raise PolicyError("score_bands: must be a list of {tier, min} entries")
    bands = []
    for band in raw:
        tier = _tier_id("score_bands", band.get("tier"))
        minimum = band.get("min")
        if not _is_whole(minimum):
            raise PolicyError(f"score_bands: {tier} min must be a whole number")
        bands.append((minimum, tier))
    if tuple(tier for _, tier in bands) != TIER_IDS:
        raise PolicyError(f"score_bands: must list {', '.join(TIER_IDS)} once each, in order")
    minimums = [minimum for minimum, _ in bands]
    if minimums[0] != 0:
        raise PolicyError("score_bands: the first min must be 0")
    if any(low >= high for low, high in zip(minimums, minimums[1:], strict=False)):
        raise PolicyError("score_bands: min values must rise strictly")
    if minimums[-1] > 100:
        raise PolicyError("score_bands: min values must not exceed 100")
    return tuple(bands)


def _rules(section: str, raw) -> tuple[Rule, ...]:
    if not isinstance(raw, list) or not all(isinstance(rule, dict) for rule in raw):
        raise PolicyError(f"{section}: must be a list of {{name, tier, when}} entries")
    rules = []
    for rule in raw:
        name = rule.get("name")
        tier = _tier_id(section, rule.get("tier"))
        when = rule.get("when")
        if not isinstance(when, dict) or not when:
            raise PolicyError(f"{section}: {name} has an empty when")
        unknown = sorted(set(when) - set(WHEN_KEYS))
        if unknown:
            raise PolicyError(f"{section}: {name} has unknown when keys {unknown}")
        rules.append(Rule(name=name, tier=tier, when=when))
    return tuple(rules)


def _tiers(raw) -> dict[str, Tier]:
    if not isinstance(raw, dict):
        raise PolicyError("tiers: must be a mapping of tier id to settings")
    for tier_id in raw:
        _tier_id("tiers", tier_id)
    tiers = {}
    for tier_id in TIER_IDS:
        settings = raw.get(tier_id)
        if not isinstance(settings, dict):
            raise PolicyError(f"tiers: {tier_id} is missing")
        if not _is_whole(settings.get("humans")) or settings["humans"] < 0:
            raise PolicyError(f"tiers: {tier_id} humans must be a whole number >= 0")
        if settings.get("agent_check") not in AGENT_CHECKS:
            raise PolicyError(f"tiers: {tier_id} agent_check must be one of {AGENT_CHECKS}")
        if settings.get("release") not in RELEASES:
            raise PolicyError(f"tiers: {tier_id} release must be one of {RELEASES}")
        tiers[tier_id] = Tier(
            id=tier_id,
            name=settings.get("name"),
            humans=settings["humans"],
            senior_human=settings.get("senior_human"),
            code_owner=settings.get("code_owner"),
            tests=settings.get("tests"),
            deploy=settings.get("deploy"),
            agent_check=settings["agent_check"],
            release=settings["release"],
        )
    demands = [RELEASES.index(tiers[tier_id].release) for tier_id in TIER_IDS]
    for lower, higher, low, high in zip(TIER_IDS, TIER_IDS[1:], demands, demands[1:], strict=False):
        if high < low:
            raise PolicyError(f"tiers: {higher} release asks less than {lower}")
    return tiers


def _window(raw) -> ObjectionWindow | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PolicyError("objection_window: must be a mapping of tiers, minutes and paths")
    tiers = raw.get("tiers")
    if not isinstance(tiers, list) or not tiers:
        raise PolicyError("objection_window: tiers must be a non-empty list")
    for tier in tiers:
        _tier_id("objection_window", tier)
        if tier in NEVER_IN_WINDOW:
            raise PolicyError(f"objection_window: {tier} can never be listed")
    paths = raw.get("paths")
    if (
        not isinstance(paths, list)
        or not paths
        or not all(isinstance(item, str) and item for item in paths)
    ):
        raise PolicyError("objection_window: paths must be a non-empty list of paths")
    minutes = raw.get("minutes")
    if not _is_whole(minutes) or minutes < 1:
        raise PolicyError("objection_window: minutes must be a whole number >= 1")
    return ObjectionWindow(tiers=tuple(tiers), minutes=minutes, paths=tuple(paths))


def _models(raw) -> Models:
    if not isinstance(raw, dict):
        raise PolicyError("models: must be a mapping of build, review and label_overrides")
    for key in ("build", "review"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise PolicyError(f"models: {key} must not be empty")
    overrides = raw.get("label_overrides") or {}
    if not isinstance(overrides, dict):
        raise PolicyError("models: label_overrides must be a mapping of label to model")
    for label in overrides:
        if not isinstance(label, str) or not label.startswith("model:"):
            raise PolicyError(f"models: override key {label!r} must start with 'model:'")
    return Models(build=raw["build"], review=raw["review"], label_overrides=dict(overrides))
