"""The release gate: whether a release may deploy, decided by rule.

A release is everything merged since the last deploy, at the highest tier among its PRs. Each
tier's ``release`` setting says what it needs: ``automatic`` deploys at once; ``checks`` needs CI
passed on the release commit, no open incident in a module it touches and not too many recent
deploys causing incidents; ``signoff`` needs the checks and then a person's approval of the
deployment. No Claude call is involved.

These are pure functions with no I/O: the caller gathers the facts and acts on the verdict. In
shadow mode the state always passes and the description says what enforce would do.
"""

from dataclasses import dataclass

from sdlc.tiers import TIER_IDS, Policy

AGENT = "release_gate"
AGENT_VERSION = "v1"
STATUS_CONTEXT = "release-gate"
DESCRIPTION_LIMIT = 140  # GitHub cuts status descriptions beyond this
STATES = {"release": "success", "hold": "pending", "blocked": "failure"}


@dataclass(frozen=True)
class OpenIncident:
    module: str
    number: int | None = None
    title: str = ""


@dataclass(frozen=True)
class ReleaseFacts:
    tier: str
    prs: tuple[int, ...]
    modules: tuple[str, ...]
    ci: str  # "passed", "failed" or "running", on the release commit
    open_incidents: tuple[OpenIncident, ...] = ()
    recent_deploys: int = 0
    failed_deploys: int = 0
    signoff: bool | None = None  # True/False in the simulated history; None: a person decides


@dataclass(frozen=True)
class Verdict:
    verdict: str  # "release", "hold" or "blocked"
    state: str
    would_be: str
    reasons: tuple[str, ...]
    environment: str
    description: str


def release_tier(tiers, fallback: str = "T2") -> str:
    """The highest tier among the release's PRs; a missing or unknown one counts as ``fallback``.

    No tiers at all give ``fallback`` too, so the gate fails closed.
    """
    known = [tier if tier in TIER_IDS else fallback for tier in tiers]
    return max(known, key=TIER_IDS.index, default=fallback)


def _reasons(policy: Policy, facts: ReleaseFacts, need: str) -> tuple[str, list[str]]:
    rules = policy.release
    verdict, reasons = "release", []
    if facts.ci == "failed":
        verdict = "blocked"
        reasons.append("CI failed on the release commit")
    elif facts.ci == "running":
        verdict = "hold"
        reasons.append("waiting for CI on the release commit")
    for incident in facts.open_incidents:
        number = f" (#{incident.number})" if incident.number is not None else ""
        reasons.append(f"open incident in {incident.module}{number}")
    recent, failed = facts.recent_deploys, facts.failed_deploys
    if recent >= rules.failure_min_deploys and recent and failed / recent > rules.failure_max:
        reasons.append(
            f"{failed} of {recent} deploys in {rules.failure_window_days} days caused incidents"
            f" (over {rules.failure_max:.0%})"
        )
    if need == "signoff" and facts.signoff is False:
        reasons.append("waiting for sign-off (simulated)")
    if reasons and verdict == "release":
        verdict = "hold"
    return verdict, reasons


def evaluate(policy: Policy, facts: ReleaseFacts, *, mode: str) -> Verdict:
    need = policy.tiers[facts.tier].release
    rules = policy.release
    environment = rules.signoff_environment if need == "signoff" else rules.environment
    if need == "automatic":
        verdict, reasons = "release", []
        text = "Release: deploys automatically"
    else:
        verdict, reasons = _reasons(policy, facts, need)
        if reasons:
            text = ("Blocked: " if verdict == "blocked" else "Held: ") + "; ".join(reasons)
        elif need == "signoff" and facts.signoff is None:
            text = "Release: checks passed; approve the deployment in GitHub"
        else:
            text = "Release: checks passed" + (
                " and signed off (simulated)" if facts.signoff else ""
            )
    would_be = STATES[verdict]
    if mode == "shadow":
        state, description = "success", f"{facts.tier} · Shadow (would be {would_be}). {text}"
    else:
        state, description = would_be, f"{facts.tier} · {text}"
    return Verdict(
        verdict=verdict,
        state=state,
        would_be=would_be,
        reasons=tuple(reasons),
        environment=environment,
        description=description[:DESCRIPTION_LIMIT],
    )


def output_of(facts: ReleaseFacts, verdict: Verdict, **extra) -> dict:
    """The verdict and the facts behind it, as the audit row's JSON ``output``."""
    return {
        "verdict": verdict.verdict,
        "would_be": verdict.would_be,
        "reasons": list(verdict.reasons),
        "environment": verdict.environment,
        "prs": list(facts.prs),
        "modules": list(facts.modules),
        "ci": facts.ci,
        "open_incidents": [
            {"module": incident.module, "number": incident.number, "title": incident.title}
            for incident in facts.open_incidents
        ],
        "recent_deploys": facts.recent_deploys,
        "failed_deploys": facts.failed_deploys,
        "signoff": facts.signoff,
        **extra,
    }
