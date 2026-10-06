"""``/tier`` overrides from PR comments: a person changes a PR's tier, under rules set in code.

Raising is always allowed and sticks across commits. Lowering needs a written reason and covers
only the commit it was made on. Nothing goes below a policy floor. These are pure functions with
no I/O: the caller reads the comments and records each ruling as an audit row.
"""

import dataclasses
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from sdlc.governance import facts_of, matching
from sdlc.tables import PullRequest
from sdlc.tiers import TIER_IDS, Policy

AGENT = "tier_override"
AGENT_VERSION = "v1"
MIN_REASON = 10
REASON_CHARS = 500
COMMAND = re.compile(r"^[ \t]*/tier[ \t]+(T[0-3])\b[ \t]*[:\-–—]?[ \t]*(.*)$", re.I | re.M)


@dataclass(frozen=True)
class Command:
    comment_id: int
    actor: str
    tier: str
    reason: str
    url: str | None


@dataclass(frozen=True)
class Ruling:
    comment_id: int
    actor: str
    reason: str
    from_tier: str
    to_tier: str
    direction: str  # raise | lower | same
    accepted: bool
    why: str
    head_sha: str

    def as_record(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "Ruling":
        return cls(**{field.name: record[field.name] for field in dataclasses.fields(cls)})


def _rank(tier: str) -> int:
    return TIER_IDS.index(tier)


def parse_commands(comments: Iterable[Mapping], agent_markers: Iterable[str]) -> list[Command]:
    """The first ``/tier`` command in each person's comment, oldest first.

    A comment containing an agent's marker never counts, even when a person's account wrote it.
    """
    markers = tuple(agent_markers)
    commands = []
    for comment in comments:
        body = comment.get("body") or ""
        user = comment.get("user") or {}
        if user.get("type") == "Bot" or any(marker in body for marker in markers):
            continue
        match = COMMAND.search(body)
        if match is None:
            continue
        commands.append(
            Command(
                comment_id=comment["id"],
                actor=user.get("login") or "unknown",
                tier=match.group(1).upper(),
                reason=match.group(2).strip()[:REASON_CHARS],
                url=comment.get("html_url"),
            )
        )
    return commands


def rule(command: Command, *, current: str, floor: str, head_sha: str) -> Ruling:
    """Accept or reject one command against the tier in force and the policy floor."""
    to = command.tier
    if _rank(to) > _rank(current):
        direction, accepted, why = "raise", True, f"Raised from {current} to {to}."
    elif to == current:
        direction, accepted, why = "same", True, f"Already {to}; nothing changed."
    elif _rank(to) < _rank(floor):
        direction, accepted = "lower", False
        why = (
            f"Rejected: a policy floor keeps this PR at {floor} or above. "
            "Floors are set in policies/tiers.yaml, not by comment."
        )
    elif len(command.reason) < MIN_REASON:
        direction, accepted = "lower", False
        why = f"Rejected: lowering a tier needs a written reason, e.g. `/tier {to} <why>`."
    else:
        direction, accepted = "lower", True
        why = f"Lowered from {current} to {to}, for this commit only."
    return Ruling(
        comment_id=command.comment_id,
        actor=command.actor,
        reason=command.reason,
        from_tier=current,
        to_tier=to,
        direction=direction,
        accepted=accepted,
        why=why,
        head_sha=head_sha,
    )


def effective_tier(agent_tier: str, floor: str, rulings: Iterable[Ruling], head_sha: str) -> str:
    """The tier in force on ``head_sha``: the agent's, then each accepted ruling in order."""
    tier = agent_tier
    for ruling in rulings:
        if not ruling.accepted:
            continue
        if ruling.direction == "raise" and _rank(ruling.to_tier) > _rank(tier):
            tier = ruling.to_tier
        elif ruling.direction == "lower" and ruling.head_sha == head_sha:
            tier = ruling.to_tier
    return max(tier, floor, key=_rank)


def describe(ruling: Ruling) -> str:
    """One line for the risk comment's overrides block."""
    reason = f' "{ruling.reason}"' if ruling.reason else ""
    return f"- `/tier {ruling.to_tier}` by @{ruling.actor}{reason}: {ruling.why}"


def floor_of(policy: Policy, pr: PullRequest) -> str:
    """The highest matching floor's tier, else T0."""
    floors = matching(policy.floors, facts_of(pr))
    return max((floor.tier for floor in floors), key=_rank, default=TIER_IDS[0])
