"""Explain a pull request's risk score, or grade the score against history.

``python -m sdlc.risk explain <pr> [--source synthetic|github]`` shows each signal's points, the
score, the tier and the reasons; ``--record`` also appends that decision to the audit trail.
``python -m sdlc.risk calibrate`` grades this database's history, and ``calibrate --generated N``
grades N generated histories, pooled. Only ``explain --record`` writes to the database.
"""

import argparse
import sys

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import calibration, scoring
from sdlc.audit import record_decision
from sdlc.db import get_engine
from sdlc.governance import Assignment, assign_tier, facts_of
from sdlc.tables import SOURCES, AgentDecision, PullRequest
from sdlc.tiers import load_policy

AGENT = "pr_risk_rubric"
AGENT_VERSION = "1"


def _record(
    db: Session,
    pr: PullRequest,
    features: scoring.Features,
    score: scoring.Score,
    assignment: Assignment,
) -> int:
    """Append the explained decision and commit it; return the new row's id."""
    subject = {"subject_type": "pr", "subject_source": pr.source, "subject_id": pr.number}
    # Explained rows have no head SHA, and a NULL never collides in the unique constraint, so
    # the attempt is numbered from the rows already recorded for this subject.
    earlier = db.scalar(
        select(func.count())
        .select_from(AgentDecision)
        .where(
            AgentDecision.agent == AGENT,
            AgentDecision.subject_type == "pr",
            AgentDecision.subject_source == pr.source,
            AgentDecision.subject_id == pr.number,
        )
    )
    row = record_decision(
        db,
        agent=AGENT,
        agent_version=AGENT_VERSION,
        trigger="manual",
        attempt=earlier + 1,
        inputs_digest=scoring.features_digest(features),
        signals=score.signals,
        raw_score=score.total,
        final_score=score.total,
        tier=assignment.tier,
        output={
            "tier": assignment.tier,
            "score_tier": assignment.score_tier,
            "floors": list(assignment.floors),
            "capped_by": assignment.capped_by,
            "reasons": list(assignment.reasons),
        },
        **subject,
    )
    db.commit()
    return row.id


def _explain(db: Session, number: int, source: str | None, record: bool = False) -> int:
    query = select(PullRequest).where(PullRequest.number == number)
    if source:
        query = query.where(PullRequest.source == source)
    prs = db.scalars(query.order_by(PullRequest.source)).all()
    if not prs:
        where = f" in {source}" if source else ""
        print(f"No pull request #{number}{where}.", file=sys.stderr)
        return 1
    if len(prs) > 1:
        sources = ", ".join(sorted({pr.source for pr in prs}))
        print(
            f"Pull request #{number} exists in more than one source ({sources}); "
            "choose one with --source.",
            file=sys.stderr,
        )
        return 1
    pr = prs[0]
    features = scoring.compute_features(db, pr)
    score = scoring.score_features(features)
    assignment = assign_tier(load_policy(), score.total, facts_of(pr))
    print(f"PR #{number} ({pr.source}): {pr.title}")
    width = max(len(name) for name in scoring.MAX_POINTS)
    for name, maximum in scoring.MAX_POINTS.items():
        print(f"  {name:<{width}}  {score.signals[name]:>3} / {maximum}")
    print(f"Score: {score.total} / 100")
    print(f"Tier: {assignment.tier}")
    print("Reasons:")
    for reason in assignment.reasons:
        print(f"  - {reason}")
    if record:
        print(f"Recorded as decision {_record(db, pr, features, score, assignment)}.")
    return 0


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.risk", description="Explain or calibrate the risk score."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    explain = commands.add_parser("explain", help="explain one pull request's score and tier")
    explain.add_argument("pr", type=int, help="pull request number")
    explain.add_argument("--source", choices=SOURCES)
    explain.add_argument(
        "--record", action="store_true", help="append the decision to the audit trail"
    )
    calibrate = commands.add_parser("calibrate", help="grade the score against history")
    calibrate.add_argument(
        "--generated", type=int, metavar="N", help="grade N generated histories, pooled"
    )
    args = parser.parse_args(argv)

    if args.command == "calibrate" and args.generated is not None:
        if args.generated < 1:
            parser.error("--generated must be at least 1")
        print(calibration.format_many(calibration.calibrate_many(load_policy(), args.generated)))
        return 0

    with Session(engine or get_engine()) as db:
        try:
            if args.command == "explain":
                return _explain(db, args.pr, args.source, args.record)
            print(calibration.format_report(calibration.calibrate(db, load_policy())))
            return 0
        finally:
            db.rollback()


if __name__ == "__main__":
    sys.exit(main())
