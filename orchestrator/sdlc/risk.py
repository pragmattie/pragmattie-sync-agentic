"""Explain a pull request's risk score, or grade the score against history. Read-only.

``python -m sdlc.risk explain <pr> [--source synthetic|github]`` shows each signal's points, the
score, the tier and the reasons. ``python -m sdlc.risk calibrate`` grades this database's history,
and ``calibrate --generated N`` grades N generated histories, pooled.
"""

import argparse
import sys

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import calibration, scoring
from sdlc.db import get_engine
from sdlc.governance import assign_tier, facts_of
from sdlc.tables import SOURCES, PullRequest
from sdlc.tiers import load_policy


def _explain(db: Session, number: int, source: str | None) -> int:
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
    score = scoring.score_pull_request(db, pr)
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
    return 0


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.risk", description="Explain or calibrate the risk score."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    explain = commands.add_parser("explain", help="explain one pull request's score and tier")
    explain.add_argument("pr", type=int, help="pull request number")
    explain.add_argument("--source", choices=SOURCES)
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
                return _explain(db, args.pr, args.source)
            print(calibration.format_report(calibration.calibrate(db, load_policy())))
            return 0
        finally:
            db.rollback()


if __name__ == "__main__":
    sys.exit(main())
