"""The ``risk-gate`` commit status: whether a pull request's tier requirements are met.

Code decides it from the tier and from what people and the AI reviewer have done, never from the
model's judgment. These are pure functions with no I/O: the caller gathers the approvals and posts
the result. In shadow mode the status always passes and its description says what it would be.
"""

from dataclasses import dataclass

from sdlc.tiers import Policy

DESCRIPTION_LIMIT = 140  # GitHub cuts status descriptions beyond this


@dataclass
class Approvals:
    signoff: bool = False
    qa_done: bool = False
    simulated_approved: bool = False
    ai_review: bool = False


@dataclass(frozen=True)
class Gate:
    state: str
    would_be: str
    missing: tuple[str, ...]
    description: str


def requirements(policy: Policy, tier: str) -> dict[str, bool]:
    settings = policy.tiers[tier]
    return {
        "signoff": settings.humans >= 1,
        "simulated_second": settings.humans >= 2,
        "manual_qa": "manual qa" in (settings.tests or "").lower(),
    }


def missing_for(
    policy: Policy, tier: str, approvals: Approvals, needs_ai_review: bool
) -> list[str]:
    """What the tier still needs, in the order a person works through it."""
    needs = requirements(policy, tier)
    missing = []
    if needs_ai_review and not needs["signoff"] and not approvals.ai_review:
        missing.append("AI review approval")
    if needs["signoff"] and not approvals.signoff:
        missing.append("human sign-off")
    if needs["manual_qa"] and not approvals.qa_done:
        missing.append("manual QA")
    if needs["simulated_second"] and not approvals.simulated_approved:
        missing.append("simulated second approval")
    return missing


def evaluate(
    policy: Policy,
    tier: str,
    *,
    ok: bool,
    approvals: Approvals,
    mode: str,
    needs_ai_review: bool = False,
) -> Gate:
    """The status to post. ``ok`` is false when the risk agent failed: the gate fails closed."""
    if not ok:
        would_be = "failure"
        missing = ["a successful risk assessment"]
        note = f"{tier} fallback: the risk agent failed, so this fails closed"
    else:
        missing = missing_for(policy, tier, approvals, needs_ai_review)
        if missing:
            would_be = "pending"
            note = f"{tier}: waiting for {', '.join(missing)}"
        else:
            would_be = "success"
            note = f"{tier}: requirements met"
    if mode == "shadow":
        state, description = "success", f"Shadow mode (would be {would_be}). {note}"
    else:
        state, description = would_be, note
    return Gate(
        state=state,
        would_be=would_be,
        missing=tuple(missing),
        description=description[:DESCRIPTION_LIMIT],
    )
