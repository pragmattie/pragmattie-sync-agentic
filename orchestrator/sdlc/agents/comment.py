"""The PR risk agent's comment: the score and why, the tier, what it needs, and the sign-off boxes.

Everything here is rendered by code from the assessment and the gate. These are pure functions
with no I/O: the caller posts the comment. ``refresh`` and ``retier`` edit an existing comment in
place, so the review stays as it was written and any ticks a person made are kept.
"""

import re
from dataclasses import dataclass

from sdlc.agents.gate import Approvals, Gate, requirements
from sdlc.agents.pr_risk import Assessment
from sdlc.agents.signoff import COMMENT_MARKER as MARKER
from sdlc.agents.signoff import HEAD_MARKER, QA_BOX, SIGNOFF_BOX
from sdlc.scoring import MAX_POINTS
from sdlc.tiers import TIER_IDS, Policy

OVERRIDES_START = "<!-- overrides -->"
OVERRIDES_END = "<!-- /overrides -->"
NEEDS_HEADING = "**What this tier needs**"
SIGNOFF_TEXT = "**Human sign-off:** I have reviewed this change"
QA_TEXT = "**Manual QA done:** I have exercised this change by hand"
GITHUB_APPROVAL = "  (A person's GitHub approval of this commit counts too.)"
OVERRIDES_HEADING = (
    "**Tier overrides** (raise with `/tier T3`; lower with `/tier T1 <written reason>`)"
)

_HEAD = re.compile(r"<!-- head:([0-9A-Za-z_-]+) -->")
_HEADING_LINE = re.compile(r"(?m)^## Risk review: .*$")
_SIMULATED = re.compile(r"(- Simulated second approval: )\*\*(?:approved|waiting)\*\*")
_SHADOW = re.compile(r"(It shows what enforce mode would do: )\*\*\w+\*\*")
_OVERRIDES = re.compile(re.escape(OVERRIDES_START) + r".*?" + re.escape(OVERRIDES_END), re.S)
_NEEDS = re.compile(re.escape(NEEDS_HEADING) + r"\n(?:[^\n]+\n)*")


@dataclass(frozen=True)
class Meta:
    decision_id: int | None
    mode: str
    approver_name: str
    head_sha: str


def comment_head(body: str | None) -> str | None:
    """The commit a comment was written for, or None."""
    match = _HEAD.search(body or "")
    return match.group(1) if match else None


def heading(policy: Policy, tier: str, agent_tier: str) -> str:
    text = f"## Risk review: {tier} ({policy.tiers[tier].name})"
    if tier != agent_tier:
        moved = "raised" if TIER_IDS.index(tier) > TIER_IDS.index(agent_tier) else "lowered"
        text += f", {moved} from {agent_tier} by a person"
    return text


def _box(done: bool, text: str) -> str:
    return f"- [{'x' if done else ' '}] {text}"


def needs_lines(policy: Policy, tier: str, approvals: Approvals, approver_name: str) -> list[str]:
    needs = requirements(policy, tier)
    lines = [NEEDS_HEADING]
    if not any(needs.values()):
        lines.append("- Nothing. A person doesn't need to act.")
        return lines
    if needs["signoff"]:
        lines += [_box(approvals.signoff, SIGNOFF_TEXT), GITHUB_APPROVAL]
    if needs["manual_qa"]:
        lines.append(_box(approvals.qa_done, QA_TEXT))
    if needs["simulated_second"]:
        state = "approved" if approvals.simulated_approved else "waiting"
        lines.append(
            f"- Simulated second approval: **{state}** "
            f"({approver_name}, controlled by the repo owner; not a real second person)"
        )
    return lines


def overrides_block(lines: list[str] | None) -> str:
    inner = [OVERRIDES_HEADING, *lines] if lines else []
    return "\n".join([OVERRIDES_START, *inner, OVERRIDES_END])


def _score_lines(assessment: Assessment) -> list[str]:
    a = assessment
    score = f"**Score {a.final_score}/100**: rubric {a.raw_score}, "
    score += f"model adjustment {a.adjustment:+d}"
    if a.clamped:
        score += " (limited to the +/-15 maximum)"
    sections = [score]
    if a.top_reasons:
        why = [f"{number}. {reason}" for number, reason in enumerate(a.top_reasons, 1)]
        sections.append("\n".join(["**Why**", *why]))
    if a.justification:
        sections.append(f"_{a.justification}_")
    if a.test_gaps:
        gaps = [f"- {gap}" for gap in a.test_gaps]
        sections.append("\n".join(["**Tests that look missing**", *gaps]))
    return sections


def _signal_table(signals: dict[str, int]) -> str:
    rows = [f"| {name} | {points}/{MAX_POINTS[name]} |" for name, points in signals.items()]
    return "\n".join(
        [
            "<details><summary>Signal points</summary>",
            "",
            "| Signal | Points |",
            "| --- | --- |",
            *rows,
            "",
            "</details>",
        ]
    )


def footer(assessment: Assessment, decision_id: int | None) -> str:
    parts = [f"decision {decision_id}" if decision_id is not None else "decision not recorded"]
    if assessment.llm is not None:
        tokens = assessment.llm.input_tokens + assessment.llm.output_tokens
        parts += [assessment.model, f"{tokens} tokens"]
    parts.append("PragMattie Sync demo")
    return f"<sub>{' · '.join(parts)}</sub>"


def render(
    assessment: Assessment,
    gate: Gate,
    approvals: Approvals,
    policy: Policy,
    meta: Meta,
    *,
    tier: str | None = None,
    override_lines: list[str] | None = None,
    tests: str | None = None,
) -> str:
    agent_tier = assessment.assignment.tier
    tier = tier or agent_tier
    sections = [
        "\n".join(
            [MARKER, HEAD_MARKER.format(sha=meta.head_sha), heading(policy, tier, agent_tier)]
        )
    ]
    if meta.mode == "shadow":
        sections.append(
            "> **Shadow mode.** This check always passes for now. "
            f"It shows what enforce mode would do: **{gate.would_be}**."
        )
    if assessment.ok:
        sections += _score_lines(assessment)
    else:
        sections.append(
            f"**The risk agent could not score this PR** ({assessment.status}). "
            f"It is treated as {tier} and the check fails closed. It will try again shortly."
        )
    if assessment.assignment.reasons:
        sections.append("\n".join(f"- {reason}" for reason in assessment.assignment.reasons))
    if tests:
        sections.append(tests)
    sections.append(overrides_block(override_lines))
    sections.append("\n".join(needs_lines(policy, tier, approvals, meta.approver_name)))
    sections.append(_signal_table(assessment.signals))
    sections.append(footer(assessment, meta.decision_id))
    return "\n\n".join(sections) + "\n"


def refresh(body: str, gate: Gate, simulated_approved: bool) -> str:
    """Update the live parts only: the simulated approval and the shadow line's "would do"."""
    state = "approved" if simulated_approved else "waiting"
    body = _SIMULATED.sub(lambda m: f"{m.group(1)}**{state}**", body)
    return _SHADOW.sub(lambda m: f"{m.group(1)}**{gate.would_be}**", body)


def retier(
    body: str,
    policy: Policy,
    *,
    tier: str,
    agent_tier: str,
    approvals: Approvals,
    approver_name: str,
    override_lines: list[str] | None,
) -> str:
    """Rewrite the heading, overrides and needs for the tier in force; keep the rest and ticks."""
    needs = _NEEDS.search(body)
    old_needs = needs.group(0) if needs else ""
    kept = Approvals(
        signoff=approvals.signoff or bool(SIGNOFF_BOX.search(old_needs)),
        qa_done=approvals.qa_done or bool(QA_BOX.search(old_needs)),
        simulated_approved=approvals.simulated_approved,
        ai_review=approvals.ai_review,
    )
    body = _HEADING_LINE.sub(lambda _: heading(policy, tier, agent_tier), body, count=1)
    block = overrides_block(override_lines)
    if _OVERRIDES.search(body):
        body = _OVERRIDES.sub(lambda _: block, body, count=1)
    else:
        body = body.replace(NEEDS_HEADING, f"{block}\n\n{NEEDS_HEADING}", 1)
    lines = "\n".join(needs_lines(policy, tier, kept, approver_name)) + "\n"
    return _NEEDS.sub(lambda _: lines, body, count=1)
