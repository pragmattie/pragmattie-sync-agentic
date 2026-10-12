"""Record the test selector's recommendation for a commit as an audit row.

``recommend`` selects the jobs, adds the flaky flags and appends one row. It flushes but never
commits: the caller commits it with the risk decision it accompanies. It writes nothing to
GitHub: CI still runs every job.
"""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc.agents.suite_select import (
    AGENT,
    AGENT_VERSION,
    Selection,
    flaky_suites,
    select_suites,
    with_flaky,
)
from sdlc.audit import TRIAL, record_decision
from sdlc.tables import AgentDecision, PullRequest
from sdlc.tiers import Policy

MAX_PATHS = 300
RESULT = "result"  # 6.2's settled rows; never a recommendation
SHOWN_IN = {"shown_in": "the risk comment's Tests section"}


def _rows_for(pr: PullRequest, sha: str):
    return (
        AgentDecision.agent == AGENT,
        AgentDecision.subject_type == "pr",
        AgentDecision.subject_source == pr.source,
        AgentDecision.subject_id == pr.number,
        AgentDecision.head_sha == sha,
    )


def recommend(
    db: Session,
    pr: PullRequest,
    sha: str,
    paths: list[str],
    *,
    tier: str,
    policy: Policy,
    assessed: bool,
    now: datetime,
    trigger: str = "poll",
) -> Selection:
    selection = select_suites(paths, tier=tier, policy=policy, assessed=assessed)
    selection = with_flaky(selection, flaky_suites(db, now))
    attempts = db.scalar(select(func.count()).select_from(AgentDecision).where(*_rows_for(pr, sha)))
    record_decision(
        db,
        agent=AGENT,
        agent_version=AGENT_VERSION,
        subject_type="pr",
        subject_source=pr.source,
        subject_id=pr.number,
        trigger=trigger,
        now=now,
        head_sha=sha,
        attempt=attempts + 1,
        tier=tier,
        output={**selection.as_record(), "paths": paths[:MAX_PATHS]},
        action_taken=dict(SHOWN_IN),
        status="ok",
    )
    return selection


def latest(db: Session, pr: PullRequest, sha: str) -> AgentDecision | None:
    """The newest recommendation for this commit, leaving out results and trials."""
    query = select(AgentDecision).where(
        *_rows_for(pr, sha), AgentDecision.trigger.not_in((RESULT, TRIAL))
    )
    return db.scalars(
        query.order_by(AgentDecision.created_at.desc(), AgentDecision.id.desc()).limit(1)
    ).first()
