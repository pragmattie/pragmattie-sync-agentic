"""Similar past issues by plain word overlap (Jaccard on title tokens), computed in code."""

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc.tables import Issue

STOPWORDS = frozenset(
    "a an the to of for in on with and or but is are be as at by from into this that it its "
    "we our add adds fix fixes support supports allow allows".split()
)
BODY_CHARS = 500
SCORE_PLACES = 3

_WORD = re.compile(r"[a-z0-9]+")


def tokens(text: str | None) -> set[str]:
    """Lowercase words, without stopwords, longer than two characters."""
    return {
        word
        for word in _WORD.findall((text or "").lower())
        if len(word) > 2 and word not in STOPWORDS
    }


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@dataclass
class SimilarIssue:
    number: int
    title: str
    module: str | None
    type: str
    estimate_points: int | None
    actual_days: float | None
    state: str
    score: float


def similar_issues(
    db: Session,
    title: str,
    body: str | None,
    *,
    exclude_number: int | None = None,
    limit: int = 10,
    source: str | None = None,
) -> list[SimilarIssue]:
    """Past issues whose titles share words with this one, best first, ties by number."""
    query = tokens(title) | tokens((body or "")[:BODY_CHARS])
    if not query:
        return []
    statement = select(Issue).where(Issue.number.is_not(None))
    if source is not None:
        statement = statement.where(Issue.source == source)
    if exclude_number is not None:
        statement = statement.where(Issue.number != exclude_number)
    scored = []
    for issue in db.scalars(statement):
        score = jaccard(query, tokens(issue.title))
        if score > 0:
            scored.append((score, issue))
    scored.sort(key=lambda item: (-item[0], item[1].number))
    return [
        SimilarIssue(
            number=issue.number,
            title=issue.title,
            module=issue.module,
            type=issue.type,
            estimate_points=issue.estimate_points,
            actual_days=issue.actual_days,
            state=issue.state,
            score=round(score, SCORE_PLACES),
        )
        for score, issue in scored[:limit]
    ]
