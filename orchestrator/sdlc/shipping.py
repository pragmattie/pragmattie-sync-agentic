"""When a merged pull request reaches production: the one shipping rule, used everywhere.

A deployment ships everything merged up to its commit. ``shipped_through`` is the ``merged_at`` of
the pull request whose merge commit is the deployment's ``sha``, or, when no PR's merge commit is,
the deployment's own time. A PR is shipped by the earliest successful deployment whose
``shipped_through`` and time are both at or after the PR's ``merged_at``, so a deploy of an older
commit never ships a newer merge. Deployments ship only work of their own source: simulated
deployments never ship real work, nor real ones simulated work.

Delivery flow (``sdlc.flow``) reads deployments from the database; the delivery board
(``sdlc.board``) has none, so it builds the ``(through, deployed_at)`` pairs from GitHub and uses
``shipped_at`` directly.
"""

from collections.abc import Iterable
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc.tables import Deployment, PullRequest


def shipped_at(
    merged_at: datetime | None, deploys: Iterable[tuple[datetime, datetime]]
) -> datetime | None:
    """When the PR merged at ``merged_at`` shipped, from ``(through, deployed_at)`` pairs."""
    if merged_at is None:
        return None
    shipping = [deployed for through, deployed in deploys if min(through, deployed) >= merged_at]
    return min(shipping, default=None)


def shipped_through(db: Session, deployment: Deployment) -> datetime:
    """The ``merged_at`` of the PR whose merge commit the deployment is, else its own time."""
    merged_at = None
    if deployment.sha:
        merged_at = db.scalar(
            select(PullRequest.merged_at).where(
                PullRequest.source == deployment.source,
                PullRequest.merge_commit_sha == deployment.sha,
                PullRequest.merged_at.is_not(None),
            )
        )
    return merged_at or deployment.deployed_at


def deploys(db: Session, source: str) -> list[tuple[datetime, datetime]]:
    """Every successful deployment of ``source`` as ``(shipped_through, deployed_at)``."""
    merged_by_sha = dict(
        db.execute(
            select(PullRequest.merge_commit_sha, PullRequest.merged_at).where(
                PullRequest.source == source,
                PullRequest.merge_commit_sha.is_not(None),
                PullRequest.merged_at.is_not(None),
            )
        ).all()
    )
    rows = db.scalars(
        select(Deployment).where(Deployment.source == source, Deployment.status == "success")
    )
    return [(merged_by_sha.get(row.sha) or row.deployed_at, row.deployed_at) for row in rows]
