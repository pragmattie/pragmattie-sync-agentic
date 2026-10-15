"""The cutover check: whether v2 has taken over governing this repository (docs/cutover.md).

``python -m sdlc.cutover check`` only reads, and prints one line for each of the runbook's steps
3 to 5: the token reaches the repository and its commit statuses, ``ORCHESTRATOR_MODE`` is
enforce, the ``agents`` service has polled lately (its newest audit row), and the newest open PR
has a ``pr_risk`` row in v2's own audit trail whose status was posted. Both tokens belong to the
same person, so GitHub's status author can't tell v1 from v2; v2's audit trail can.

Whether the token may write statuses can't be tested without writing one, so the check says so
and step 5 confirms it. Exit code 1 means at least one check failed; a WARN doesn't.
"""

import argparse
import sys
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.agents import planner, pr_risk
from sdlc.audit import TRIAL
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.demo_check import FAIL, PASS, WARN, Check, line
from sdlc.forecaster import AGENT as FORECASTER
from sdlc.github_client import GitHubClient, GitHubError
from sdlc.signals.github import Collector
from sdlc.tables import AgentDecision

SOURCE = Collector.source
POLL_WINDOW = timedelta(minutes=5)
# the collector's agents, which write audit rows without the agents service
NOT_POLLING = (FORECASTER, planner.AGENT)
WRITE_UNTESTED = (
    "can't be tested without writing a status: step 5 confirms it "
    "(the next PR gets v2's risk-gate)."
)


def github_checks(gh: Any) -> list[Check]:
    try:
        repository = gh.get("/repos/{repo}")
    except GitHubError as error:
        detail = f"{error}. Check GITHUB_TOKEN and GITHUB_REPO in .env (step 3)."
        return [Check("Token: repository", FAIL, detail)]
    checks = [Check("Token: repository", PASS, f"reaches {repository['full_name']}")]
    branch = repository.get("default_branch", "main")
    try:
        gh.get(f"/repos/{{repo}}/commits/{quote(branch, safe='')}/statuses")
        detail = f"can read the statuses of {branch}'s head"
        checks.append(Check("Token: statuses", PASS, detail))
    except GitHubError as error:
        detail = f"{error}. The token needs Commit statuses (step 3)."
        checks.append(Check("Token: statuses", FAIL, detail))
    checks.append(Check("Token: write statuses", WARN, WRITE_UNTESTED))
    return checks


def mode_check(mode: str) -> Check:
    if mode == "enforce":
        return Check("ORCHESTRATOR_MODE", PASS, "enforce: v2 posts and gates")
    detail = f"{mode}: set ORCHESTRATOR_MODE=enforce in v2's .env and recreate agents (step 5)."
    return Check("ORCHESTRATOR_MODE", FAIL, detail)


def poll_check(db: Session, now: datetime) -> Check:
    newest = db.scalar(
        select(AgentDecision.created_at)
        .where(
            AgentDecision.subject_source == SOURCE,
            AgentDecision.trigger != TRIAL,
            AgentDecision.agent.not_in(NOT_POLLING),
        )
        .order_by(AgentDecision.created_at.desc())
        .limit(1)
    )
    if newest is None:
        detail = "no audit row from the agents service yet: is it running (step 5)?"
        return Check("Agents polling", FAIL, detail)
    age = now - newest
    minutes = int(age.total_seconds() // 60)
    if age <= POLL_WINDOW:
        return Check("Agents polling", PASS, f"newest audit row {minutes} min ago")
    detail = (
        f"newest audit row {minutes} min ago: check the agents service's log, or open a test PR "
        "from a demo- branch if nothing has happened to decide (step 5)."
    )
    return Check("Agents polling", FAIL, detail)


def newest_open_pr(gh: Any) -> dict | None:
    """The newest open PR the poll loop scores (it skips drafts)."""
    listed = [pr for pr in gh.paginate("/repos/{repo}/pulls", state="open") if not pr.get("draft")]
    return max(listed, key=lambda pr: pr["number"]) if listed else None


def posted(row: AgentDecision) -> bool:
    status = (row.action_taken or {}).get("status")
    return isinstance(status, dict) and "done" in status


def risk_gate_check(db: Session, pr: dict | None) -> Check:
    if pr is None:
        return Check("v2's risk-gate", PASS, "no open PR to check")
    number, sha = pr["number"], pr["head"]["sha"]
    rows = db.scalars(
        select(AgentDecision).where(
            AgentDecision.agent == pr_risk.AGENT,
            AgentDecision.subject_type == "pr",
            AgentDecision.subject_source == SOURCE,
            AgentDecision.subject_id == number,
            AgentDecision.head_sha == sha,
            AgentDecision.trigger != TRIAL,
        )
    ).all()
    if any(posted(row) for row in rows):
        return Check("v2's risk-gate", PASS, f"v2 posted it on #{number} at {sha[:7]}")
    found = "none was posted" if rows else "no pr_risk row"
    detail = (
        f"#{number} at {sha[:7]}: {found}. A commit scored before cutover keeps its row: push "
        "one, or open a test PR from a demo- branch (step 5)."
    )
    return Check("v2's risk-gate", FAIL, detail)


def run_checks(
    now: datetime, *, engine: Engine | None = None, gh: Any = None, mode: str | None = None
) -> list[Check]:
    gh = gh or GitHubClient()
    checks = github_checks(gh)
    checks.append(mode_check(mode or get_settings().orchestrator_mode))
    with Session(engine or get_engine()) as db:
        checks.append(poll_check(db, now))
        if checks[0].state == FAIL:
            detail = "skipped: GitHub isn't reachable."
            checks.append(Check("v2's risk-gate", FAIL, detail))
        else:
            checks.append(risk_gate_check(db, newest_open_pr(gh)))
    return checks


def main(argv: list[str] | None = None, **kwargs: Any) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.cutover", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", choices=["check"])
    parser.parse_args(argv)

    checks = run_checks(utcnow(), **kwargs)
    for check in checks:
        print(line(check))
    failed = sum(1 for check in checks if check.state == FAIL)
    print(f"Not cut over: {failed} check(s) failed." if failed else "v2 governs this repository.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
