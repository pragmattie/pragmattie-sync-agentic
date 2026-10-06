"""The simulated second approver: it fills a seat a tier needs when there is no second person.

It approves only when the repository owner tells it to. The runner asks it for an approval when a
pull request's tier needs one; nothing approves automatically. Every approval is stored with
``source`` "simulated" and shown as simulated, never as a real person's.

``python -m sdlc.approver pending`` lists what is waiting, ``request <pr> [--tier T3] [--reason]``
asks for an approval by hand, and ``approve <pr> [--note]`` gives one. Each takes ``--source`` and
``--approver``.
"""

import argparse
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.clock import utcnow
from sdlc.db import get_engine
from sdlc.tables import SOURCES, Approval, PullRequest
from sdlc.tiers import TIER_IDS

POLICY = Path(__file__).resolve().parent.parent / "policies" / "approvers.yaml"


class ApproverError(ValueError):
    """The approvers file is invalid, or an approval can't be requested or given."""


@dataclass(frozen=True)
class Approver:
    id: str
    name: str
    controlled_by: str
    applies_to_tiers: tuple[str, ...]


def load_approvers(path: Path = POLICY) -> tuple[Approver, ...]:
    try:
        with open(path, encoding="utf-8") as file:
            data = yaml.safe_load(file)
    except FileNotFoundError as error:
        raise ApproverError(f"approvers: no approvers file at {path}") from error
    except yaml.YAMLError as error:
        raise ApproverError(f"approvers: {path} is not valid YAML: {error}") from error
    raw = data.get("approvers") if isinstance(data, dict) else None
    if not isinstance(raw, list) or not raw or not all(isinstance(item, dict) for item in raw):
        raise ApproverError("approvers: must be a non-empty list of approvers")
    approvers = []
    for item in raw:
        for key in ("id", "name", "controlled_by"):
            if not isinstance(item.get(key), str) or not item[key].strip():
                raise ApproverError(f"approvers: every approver needs a {key}")
        tiers = item.get("applies_to_tiers")
        if not isinstance(tiers, list) or not tiers or any(t not in TIER_IDS for t in tiers):
            raise ApproverError(
                f"approvers: {item['id']} applies_to_tiers must list tiers from {TIER_IDS}"
            )
        approvers.append(
            Approver(
                id=item["id"],
                name=item["name"],
                controlled_by=item["controlled_by"],
                applies_to_tiers=tuple(tiers),
            )
        )
    ids = [approver.id for approver in approvers]
    if len(set(ids)) != len(ids):
        raise ApproverError("approvers: ids must be unique")
    return tuple(approvers)


def resolve_approver(approvers: tuple[Approver, ...], approver_id: str | None = None) -> Approver:
    """The approver with this id, or the only one when no id is given."""
    ids = ", ".join(approver.id for approver in approvers)
    if approver_id is None:
        if len(approvers) != 1:
            raise ApproverError(f"There are several approvers ({ids}); choose one with --approver.")
        return approvers[0]
    for approver in approvers:
        if approver.id == approver_id:
            return approver
    raise ApproverError(f"No approver {approver_id!r}; the approvers are: {ids}.")


def find_pull_request(db: Session, number: int, source: str | None = None) -> PullRequest:
    query = select(PullRequest).where(PullRequest.number == number)
    if source:
        query = query.where(PullRequest.source == source)
    prs = db.scalars(query.order_by(PullRequest.source)).all()
    if not prs:
        where = f" in {source}" if source else ""
        raise ApproverError(f"No pull request #{number}{where}.")
    if len(prs) > 1:
        sources = ", ".join(sorted({pr.source for pr in prs}))
        raise ApproverError(
            f"Pull request #{number} exists in more than one source ({sources}); "
            "choose one with --source."
        )
    return prs[0]


def _row(db: Session, pr: PullRequest, approver: Approver) -> Approval | None:
    return db.scalar(
        select(Approval).where(
            Approval.pull_request_id == pr.id, Approval.approver_id == approver.id
        )
    )


def request_approval(
    db: Session,
    pr: PullRequest,
    approver: Approver,
    tier: str,
    reason: str | None = None,
    now: datetime | None = None,
) -> Approval:
    """Ask the approver to approve this PR: once a PR, only while it is open, only at its tiers."""
    if tier not in approver.applies_to_tiers:
        covered = ", ".join(approver.applies_to_tiers)
        raise ApproverError(f"{approver.name} covers only {covered}, not {tier}.")
    if pr.state != "open":
        raise ApproverError(f"Pull request #{pr.number} is {pr.state}, not open.")
    if _row(db, pr, approver) is not None:
        raise ApproverError(f"{approver.name} was already asked to approve #{pr.number}.")
    row = Approval(
        pull_request_id=pr.id,
        approver_id=approver.id,
        tier=tier,
        reason=reason,
        requested_at=now or utcnow(),
    )
    db.add(row)
    db.flush()
    return row


def approve(
    db: Session,
    pr: PullRequest,
    approver: Approver,
    note: str | None = None,
    now: datetime | None = None,
) -> Approval:
    """Approve a requested PR as the simulated approver.

    Only ever run on the repository owner's explicit instruction: nothing calls this by itself.
    """
    row = _row(db, pr, approver)
    if row is None:
        raise ApproverError(f"{approver.name} was never asked to approve #{pr.number}.")
    if row.status == "approved":
        raise ApproverError(f"{approver.name} has already approved #{pr.number}.")
    row.status = "approved"
    row.note = note
    row.decided_at = now or utcnow()
    db.flush()
    return row


def pending(
    db: Session, approver_id: str | None = None, source: str | None = None
) -> list[Approval]:
    """The pending approvals on open pull requests, oldest first."""
    query = (
        select(Approval)
        .join(PullRequest, Approval.pull_request_id == PullRequest.id)
        .where(Approval.status == "pending", PullRequest.state == "open")
    )
    if approver_id:
        query = query.where(Approval.approver_id == approver_id)
    if source:
        query = query.where(PullRequest.source == source)
    return list(db.scalars(query.order_by(Approval.requested_at, Approval.id)))


def describe_pending(approvals: list[Approval]) -> str:
    if not approvals:
        return "No approvals are waiting for you."
    count = len(approvals)
    noun = "approval" if count == 1 else "approvals"
    lines = [f"{count} {noun} waiting for you (simulated, so not real human approvals):"]
    for row in approvals:
        pr = row.pull_request
        reason = f": {row.reason}" if row.reason else ""
        asked = row.requested_at.strftime("%Y-%m-%d %H:%M")
        lines.append(
            f"  - #{pr.number} ({pr.source}) {pr.title} [{row.tier}, asked {asked} UTC]{reason}"
        )
    lines.append("Approve one with: python -m sdlc.approver approve <pr> [--source SOURCE]")
    return "\n".join(lines)


def _run(db: Session, args: argparse.Namespace, approver: Approver) -> int:
    if args.command == "pending":
        print(describe_pending(pending(db, approver.id, args.source)))
        return 0
    pr = find_pull_request(db, args.pr, args.source)
    if args.command == "request":
        request_approval(db, pr, approver, args.tier, reason=args.reason)
        db.commit()
        print(f"Asked {approver.name} to approve #{pr.number} ({pr.source}) at {args.tier}.")
        return 0
    approve(db, pr, approver, note=args.note)
    db.commit()
    print(
        f"{approver.name} approved #{pr.number} ({pr.source}). "
        "This is a simulated approval, not a real person's."
    )
    return 0


def main(argv: list[str] | None = None, engine: Engine | None = None, policy: Path = POLICY) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.approver", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--source", choices=SOURCES)
    shared.add_argument("--approver", help="the approver's id; needed only when there are several")
    commands.add_parser("pending", parents=[shared], help="list the approvals waiting for you")
    request = commands.add_parser("request", parents=[shared], help="ask for an approval")
    request.add_argument("pr", type=int, help="pull request number")
    request.add_argument("--tier", choices=TIER_IDS, default="T3")
    request.add_argument("--reason")
    give = commands.add_parser("approve", parents=[shared], help="give a simulated approval")
    give.add_argument("pr", type=int, help="pull request number")
    give.add_argument("--note")
    args = parser.parse_args(argv)

    with Session(engine or get_engine()) as db:
        try:
            approver = resolve_approver(load_approvers(policy), args.approver)
            return _run(db, args, approver)
        except ApproverError as error:
            db.rollback()
            print(str(error), file=sys.stderr)
            return 1


if __name__ == "__main__":
    sys.exit(main())
