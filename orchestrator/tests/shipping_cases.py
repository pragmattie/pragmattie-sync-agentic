"""Shipping cases shared by the shipping, Delivery flow and board tests, so all three agree.

Three real PRs merged a day apart; each case deploys commits and says when the middle one
(``MERGED``, merge commit ``SHIPPED``) ships, or ``None``.
"""

from dataclasses import dataclass
from datetime import datetime

OLDER, SHIPPED, NEWER, UNKNOWN = "o" * 40, "a" * 40, "n" * 40, "f" * 40
MERGES = {
    OLDER: datetime(2026, 9, 24, 10),
    SHIPPED: datetime(2026, 9, 25, 15),
    NEWER: datetime(2026, 9, 26, 10),
}
MERGED = MERGES[SHIPPED]


@dataclass(frozen=True)
class Deploy:
    sha: str
    at: datetime
    source: str = "github"
    succeeded: bool = True


@dataclass(frozen=True)
class Case:
    name: str
    deploys: tuple[Deploy, ...]
    shipped: datetime | None


CASES = [
    Case("no deploy", (), None),
    Case(
        "a deploy of its merge commit",
        (Deploy(SHIPPED, datetime(2026, 9, 25, 16)),),
        datetime(2026, 9, 25, 16),
    ),
    Case(
        "a deploy of a later commit",
        (Deploy(NEWER, datetime(2026, 9, 26, 12)),),
        datetime(2026, 9, 26, 12),
    ),
    Case(
        "a deploy of a commit no PR merged, after the merge",
        (Deploy(UNKNOWN, datetime(2026, 9, 25, 17)),),
        datetime(2026, 9, 25, 17),
    ),
    Case("a deploy of an older commit", (Deploy(OLDER, datetime(2026, 9, 25, 18)),), None),
    Case(
        "an older commit, then a later one",
        (Deploy(OLDER, datetime(2026, 9, 25, 18)), Deploy(NEWER, datetime(2026, 9, 27, 8))),
        datetime(2026, 9, 27, 8),
    ),
    Case("a deploy before the merge", (Deploy(UNKNOWN, datetime(2026, 9, 25, 12)),), None),
    Case(
        "a failed deploy",
        (Deploy(SHIPPED, datetime(2026, 9, 25, 16), succeeded=False),),
        None,
    ),
    Case(
        "a simulated deploy",
        (Deploy(SHIPPED, datetime(2026, 9, 25, 16), source="synthetic"),),
        None,
    ),
]
IDS = [case.name for case in CASES]
