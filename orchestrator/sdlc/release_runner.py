"""The release runner: judge each commit the deploy workflow is waiting on, and post its verdict.

The deploy workflow (``.github/workflows/deploy.yml``) runs on every push to
``main`` and waits for a ``release-gate`` commit status. Each poll this runner collects
deployments and incidents, finds the deploy runs still waiting (``JUDGING``), and judges each
commit with the release gate's rules (``sdlc.agents.release_gate``):

- **The release** is everything merged since the last successful deploy, up to and including the
  PR whose merge commit is the waiting commit (the head PR). A commit no PR merged (a direct push)
  has no PRs and gets the fallback tier.
- **Each PR's tier** is the tier in force at merge (its ``sdlc_gate_status`` row, overrides
  included), else its latest ok risk decision's, else the rubric's. The release takes the highest.
- **The facts:** CI on the commit, open incidents in the release's modules, and recent deploys
  over the policy's window with how many of them an incident links to. A person signs off in
  GitHub ("Approve deployment" on the sign-off environment), so ``signoff`` is ``None``.

A verdict is posted as the commit's ``release-gate`` status, and recorded as one audit row each
time it changes. What this process last posted is kept in memory: an unchanged verdict is never
posted again, except once after a restart (with no new row).

``ORCHESTRATOR_MODE`` switches it like every agent: ``off`` does nothing at all, ``shadow`` posts
success with what enforce would do (a T3 still deploys through the sign-off environment, since
its description starts with T3), ``enforce`` posts the real state.

``python -m sdlc.release_runner once`` polls once and prints the summary.
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import audit, scoring, suite_selector
from sdlc.agents import pr_risk, release_gate
from sdlc.agents.github_effects import Effects
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.github_client import GitHubError
from sdlc.governance import assign_tier, facts_of
from sdlc.shipping import shipped_through
from sdlc.signals.github import Collector
from sdlc.tables import AgentDecision, Deployment, GateStatus, Incident, PullRequest
from sdlc.tiers import Policy, load_policy

log = logging.getLogger("sdlc.release_runner")

DEPLOY_WORKFLOW = "deploy.yml"
# A run "waiting" for approval has already passed the gate; a completed run is never judged again.
JUDGING = {"queued", "requested", "pending", "in_progress"}
RUNS_READ = 20
MERGED_READ = 20
SOURCE = Collector.source


class ReleaseRunner:
    def __init__(self, gh: Any, policy: Policy, mode: str, *, engine: Engine | None = None):
        self.gh = gh
        self.policy = policy
        self.mode = mode
        self.effects = Effects(gh, mode)
        self.engine = engine
        self._posted: dict[str, tuple[str, str]] = {}  # the last (state, description) per commit

    def poll_once(self, now: datetime | None = None) -> dict[str, Any]:
        if self.mode == "off":
            return {"mode": "off"}
        now = now or utcnow()
        summary = {"mode": self.mode, "releases": 0, "assessed": 0, "errors": 0}
        with Session(self.engine or get_engine()) as db:
            collector = Collector(db, self.gh)
            rules = self.policy.release
            collector.collect_deployments([rules.environment, rules.signoff_environment])
            collector.collect_incidents()
            db.commit()
            shas = self.waiting_commits()
            if shas:
                self.refresh_merged(db, collector)
                db.commit()
            for sha in shas:
                summary["releases"] += 1
                try:
                    if self.judge(db, collector, sha, now):
                        summary["assessed"] += 1
                    db.commit()
                except GitHubError as error:
                    db.rollback()
                    summary["errors"] += 1
                    log.warning("release %s: %s", sha[:7], error)
        return summary

    def waiting_commits(self) -> list[str]:
        """The commits of deploy runs still waiting for a verdict, newest first."""
        path = f"/repos/{{repo}}/actions/workflows/{DEPLOY_WORKFLOW}/runs"
        try:
            runs = self.gh.get(path, branch="main", per_page=RUNS_READ)["workflow_runs"]
        except GitHubError as error:
            if str(error).startswith("GitHub 404"):
                return []  # no deploy workflow yet
            raise
        shas: list[str] = []
        for run in sorted(runs, key=lambda run: run.get("created_at") or "", reverse=True):
            if run.get("status") in JUDGING and run["head_sha"] not in shas:
                shas.append(run["head_sha"])
        return shas

    def refresh_merged(self, db: Session, collector: Collector) -> None:
        """Collect recently merged PRs not yet stored, or stored without their merge commit.

        The risk agent polls only open PRs, so a merge is otherwise seen late.
        """
        listed = self.gh.get(
            "/repos/{repo}/pulls",
            state="closed",
            sort="updated",
            direction="desc",
            per_page=MERGED_READ,
        )
        for item in listed:
            if not item.get("merged_at"):
                continue
            stored = _stored_pr(db, item["number"])
            if stored is None or stored.merge_commit_sha is None:
                collector.collect_pull_request(item)

    def head_pr(self, db: Session, collector: Collector, sha: str) -> PullRequest | None:
        """The merged PR whose merge commit is ``sha``, or None for a direct push."""
        stored = db.scalar(
            select(PullRequest).where(
                PullRequest.source == SOURCE,
                PullRequest.merge_commit_sha == sha,
                PullRequest.merged_at.is_not(None),
            )
        )
        if stored is not None:
            return stored
        match = None
        for item in self.gh.paginate(f"/repos/{{repo}}/commits/{sha}/pulls"):
            if not item.get("merged_at"):
                continue
            pr = collector.collect_pull_request(item)
            if pr.merge_commit_sha == sha:
                match = pr
        return match

    def release_prs(self, db: Session, head: PullRequest | None) -> list[PullRequest]:
        """What the release ships: the PRs merged since the last deploy, through ``head``."""
        if head is None:
            return []
        deployments = db.scalars(
            select(Deployment).where(Deployment.source == SOURCE, Deployment.status == "success")
        ).all()
        if not deployments:
            return [head]
        since = max(shipped_through(db, deployment) for deployment in deployments)
        merged = db.scalars(
            select(PullRequest).where(
                PullRequest.source == SOURCE,
                PullRequest.merged_at > since,
                PullRequest.merged_at <= head.merged_at,
            )
        ).all()
        return sorted({*merged, head}, key=lambda pr: (pr.merged_at, pr.number))

    def tier_of(self, db: Session, pr: PullRequest) -> str:
        """The tier in force at merge, else the latest ok risk decision's, else the rubric's."""
        gate = db.scalar(select(GateStatus.tier).where(GateStatus.pull_request_id == pr.id))
        if gate:
            return gate
        decision = audit.latest_decision(
            db, pr_risk.AGENT, subject_type="pr", subject_source=SOURCE, subject_id=pr.number
        )
        if decision is not None and decision.tier:
            return decision.tier
        score = scoring.score_pull_request(db, pr).total
        return assign_tier(self.policy, score, facts_of(pr)).tier

    def facts(
        self, db: Session, sha: str, prs: list[PullRequest], now: datetime
    ) -> release_gate.ReleaseFacts:
        tier = release_gate.release_tier(
            [self.tier_of(db, pr) for pr in prs], self.policy.fallback_tier
        )
        modules = sorted({pr.module for pr in prs if pr.module})
        results = suite_selector.ci_results(self.gh, sha)
        if results is None:
            ci = "running"
        elif any(result["conclusion"] == "failure" for result in results.values()):
            ci = "failed"
        else:
            ci = "passed"
        incidents = db.scalars(
            select(Incident)
            .where(
                Incident.source == SOURCE,
                Incident.resolved_at.is_(None),
                Incident.module.in_(modules),
            )
            .order_by(Incident.opened_at, Incident.id)
        )
        open_incidents = tuple(
            release_gate.OpenIncident(incident.module, _issue_number(incident), incident.title)
            for incident in incidents
        )
        window_start = now - timedelta(days=self.policy.release.failure_window_days)
        recent = list(
            db.scalars(
                select(Deployment.id).where(
                    Deployment.source == SOURCE,
                    Deployment.status == "success",
                    Deployment.deployed_at >= window_start,
                    Deployment.deployed_at <= now,
                )
            )
        )
        failed = (
            db.scalar(
                select(func.count(func.distinct(Incident.deployment_id))).where(
                    Incident.deployment_id.in_(recent)
                )
            )
            if recent
            else 0
        )
        return release_gate.ReleaseFacts(
            tier=tier,
            prs=tuple(sorted(pr.number for pr in prs)),
            modules=tuple(modules),
            ci=ci,
            open_incidents=open_incidents,
            recent_deploys=len(recent),
            failed_deploys=failed,
            signoff=None,
        )

    def judge(self, db: Session, collector: Collector, sha: str, now: datetime) -> bool:
        """Evaluate the release ending at ``sha``; post and record it as needed.

        Returns whether a new verdict was recorded.
        """
        head = self.head_pr(db, collector, sha)
        facts = self.facts(db, sha, self.release_prs(db, head), now)
        verdict = release_gate.evaluate(self.policy, facts, mode=self.mode)
        posted = (verdict.state, verdict.description)
        rows = _rows_for(db, sha)
        last = rows[-1] if rows else None
        recorded = last is not None and _posted_of(last) == posted
        if recorded and self._posted.get(sha) == posted:
            return False
        effect = self.effects.set_status(
            sha, verdict.state, verdict.description, context=release_gate.STATUS_CONTEXT
        )
        if "done" in effect:
            self._posted[sha] = posted
        if recorded:
            return False
        audit.record_decision(
            db,
            agent=release_gate.AGENT,
            agent_version=release_gate.AGENT_VERSION,
            subject_type="release",
            subject_source=SOURCE,
            subject_id=head.number if head is not None else 0,
            head_sha=sha,
            attempt=max((row.attempt for row in rows), default=0) + 1,
            trigger="poll",
            now=now,
            tier=facts.tier,
            output=release_gate.output_of(facts, verdict, mode=self.mode),
            action_taken={"state": verdict.state, "description": verdict.description, **effect},
        )
        return True


def _stored_pr(db: Session, number: int) -> PullRequest | None:
    return db.scalar(
        select(PullRequest).where(
            PullRequest.source == SOURCE, PullRequest.external_id == f"pr-{number}"
        )
    )


def _issue_number(incident: Incident) -> int | None:
    prefix, _, number = (incident.external_id or "").partition("-")
    return int(number) if prefix == "issue" and number.isdigit() else None


def _rows_for(db: Session, sha: str) -> list[AgentDecision]:
    """This commit's release verdicts, oldest first, trial rows left out."""
    query = select(AgentDecision).where(
        AgentDecision.agent == release_gate.AGENT,
        AgentDecision.subject_type == "release",
        AgentDecision.subject_source == SOURCE,
        AgentDecision.head_sha == sha,
        AgentDecision.trigger != audit.TRIAL,
    )
    return list(db.scalars(query.order_by(AgentDecision.created_at, AgentDecision.id)))


def _posted_of(row: AgentDecision) -> tuple[str | None, str | None]:
    action = row.action_taken or {}
    return action.get("state"), action.get("description")


def build(mode: str, *, gh: Any = None, engine: Engine | None = None) -> ReleaseRunner:
    if gh is None:
        from sdlc.github_client import GitHubClient

        gh = GitHubClient()
    return ReleaseRunner(gh, load_policy(), mode, engine=engine)


def main(argv: list[str] | None = None, runner: ReleaseRunner | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.release_runner", description=__doc__.split("\n")[0]
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("once", help="poll once and print the summary")
    parser.parse_args(argv)

    try:
        runner = runner or build(get_settings().orchestrator_mode)
    except GitHubError as error:
        sys.exit(str(error))
    print(json.dumps(runner.poll_once()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
