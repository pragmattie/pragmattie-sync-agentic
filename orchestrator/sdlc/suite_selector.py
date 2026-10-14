"""Record the test selector's recommendation for a commit as an audit row, and grade it against
what CI really did on that commit.

``recommend`` selects the jobs, adds the flaky flags and appends one row. It flushes but never
commits: the caller commits it with the risk decision it accompanies. It writes nothing to
GitHub: CI still runs every job.

``settle`` reads each recent recommendation's CI jobs from GitHub Actions and, once every job has
finished, appends one ``ci_result`` row: what skipping the jobs it left out would have missed,
the failures it would have caught, and the CI time it would have saved. It only reads GitHub.

``python -m sdlc.suite_selector report`` prints the track record from those rows.
"""

import argparse
import sys
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.agents.suite_select import (
    AGENT,
    AGENT_VERSION,
    CI_SUITES,
    Selection,
    flaky_suites,
    select_suites,
    with_flaky,
)
from sdlc.audit import TRIAL, record_decision
from sdlc.db import get_engine
from sdlc.github_client import parse_time
from sdlc.signals.github import SUITES
from sdlc.tables import AgentDecision, PullRequest
from sdlc.tiers import Policy

MAX_PATHS = 300
RESULT = "ci_result"  # a settled row; never a recommendation
SETTLE_WINDOW = timedelta(days=7)
FINISHED = ("success", "failure")
SHOWN_IN = {"shown_in": "the risk comment's Tests section"}
OWN_SOURCES = ("github", "synthetic")  # rows imported from v1 are history, not ours to settle


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


def ci_results(gh, sha: str) -> dict | None:
    """Each CI job's final conclusion on this commit, or ``None`` until every job has finished."""
    runs = gh.get("/repos/{repo}/actions/runs", head_sha=sha, per_page=100)["workflow_runs"]
    attempts: dict[str, list[tuple[int, int, dict]]] = {suite: [] for suite in CI_SUITES}
    for order, run in enumerate(sorted(runs, key=lambda run: run["created_at"])):
        path = f"/repos/{{repo}}/actions/runs/{run['id']}/jobs"
        for job in gh.paginate(path, key="jobs", filter="all"):
            suite = SUITES.get(job["name"])
            if suite in attempts:
                attempts[suite].append((order, job.get("run_attempt", 1), job))
    results = {}
    for suite, jobs in attempts.items():
        jobs = [job for *_, job in sorted(jobs, key=lambda item: item[:2])]
        if not jobs:
            return None
        last = jobs[-1]
        final = last.get("conclusion") if last.get("status") == "completed" else None
        if final not in FINISHED:
            return None
        started = parse_time(last.get("started_at"))
        completed = parse_time(last.get("completed_at"))
        results[suite] = {
            "conclusion": final,
            "flaky": final == "success"
            and any(job.get("conclusion") == "failure" for job in jobs[:-1]),
            "seconds": int((completed - started).total_seconds()) if started and completed else 0,
        }
    return results


def _key(row: AgentDecision) -> tuple[str, int, str | None]:
    return row.subject_source, row.subject_id, row.head_sha


def unsettled(db: Session, now: datetime) -> list[AgentDecision]:
    """The newest recommendation for each recent commit that has no ``ci_result`` row yet."""
    query = select(AgentDecision).where(
        AgentDecision.agent == AGENT,
        AgentDecision.subject_type == "pr",
        AgentDecision.subject_source.in_(OWN_SOURCES),
        AgentDecision.trigger != TRIAL,
        AgentDecision.created_at >= now - SETTLE_WINDOW,
    )
    settled: set[tuple] = set()
    newest: dict[tuple, AgentDecision] = {}
    for row in db.scalars(query.order_by(AgentDecision.id)):
        if row.trigger == RESULT:
            settled.add(_key(row))
        else:
            newest[_key(row)] = row
    return [row for key, row in newest.items() if key not in settled]


def settle(db: Session, gh, now: datetime) -> int:
    """Record a ``ci_result`` row for each recommendation whose commit has finished CI.

    It flushes but never commits; a ``GitHubError`` is left to the caller."""
    recorded = 0
    for row in unsettled(db, now):
        results = ci_results(gh, row.head_sha)
        if results is None:
            continue
        suites, skipped = row.output["suites"], row.output["skipped"]
        missed = [suite for suite in skipped if results[suite]["conclusion"] == "failure"]
        attempts = db.scalar(
            select(func.count())
            .select_from(AgentDecision)
            .where(
                AgentDecision.agent == AGENT,
                AgentDecision.subject_type == "pr",
                AgentDecision.subject_source == row.subject_source,
                AgentDecision.subject_id == row.subject_id,
                AgentDecision.head_sha == row.head_sha,
            )
        )
        record_decision(
            db,
            agent=AGENT,
            agent_version=AGENT_VERSION,
            subject_type="pr",
            subject_source=row.subject_source,
            subject_id=row.subject_id,
            trigger=RESULT,
            now=now,
            head_sha=row.head_sha,
            attempt=attempts + 1,
            tier=row.tier,
            output={
                "recommendation_id": row.id,
                "suites": suites,
                "skipped": skipped,
                "results": results,
                "missed": missed,
                "caught": [s for s in suites if results[s]["conclusion"] == "failure"],
                "saved_seconds": sum(results[suite]["seconds"] for suite in skipped),
            },
            status="missed" if missed else "ok",
        )
        recorded += 1
    return recorded


def report(db: Session) -> dict:
    """The track record from every ``ci_result`` row of v2's own."""
    query = select(AgentDecision).where(
        AgentDecision.agent == AGENT,
        AgentDecision.subject_source.in_(OWN_SOURCES),
        AgentDecision.trigger == RESULT,
    )
    rows = list(db.scalars(query.order_by(AgentDecision.id)))
    return {
        "commits": len(rows),
        "run": sum(len(row.output["suites"]) for row in rows),
        "skipped": sum(len(row.output["skipped"]) for row in rows),
        "misses": [(row.subject_id, suite) for row in rows for suite in row.output["missed"]],
        "caught": sum(len(row.output["caught"]) for row in rows),
        "flaky": sum(
            1 for row in rows for result in row.output["results"].values() if result["flaky"]
        ),
        "saved_minutes": round(sum(row.output["saved_seconds"] for row in rows) / 60, 1),
    }


def describe(record: dict) -> str:
    n = record["commits"]
    if not n:
        return "No commits have finished CI since the test selector started recommending."
    lines = [
        f"Test selector track record: {n} {'commit' if n == 1 else 'commits'} with CI results.",
        f"  Would have run {record['run']} jobs and skipped {record['skipped']}, "
        f"saving about {record['saved_minutes']} CI minutes.",
        f"  Skipped jobs that really failed (misses): {len(record['misses'])}",
        *(f"    PR #{pr}: {suite}" for pr, suite in record["misses"]),
        f"  Failures caught by jobs it would have run: {record['caught']}",
        f"  Jobs that failed and then passed on a re-run (flaky): {record['flaky']}",
        "  CI still runs every job, so these are what skipping would have done, not what it did.",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.suite_selector", description="The test selector's track record."
    )
    parser.add_argument("command", choices=["report"])
    parser.parse_args(argv)
    with Session(engine or get_engine()) as db:
        print(describe(report(db)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
