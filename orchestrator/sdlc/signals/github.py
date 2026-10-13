"""Real work from a GitHub repository: milestones (as epics), issues, pull requests with their
files and reviews, CI jobs, deployments and incidents.

CI jobs come only from the CI workflow (``CI_WORKFLOW``); the other workflows' jobs aren't CI.
A finished run's jobs are read once per collector process (``CIMemory``), and each collection that
stores CI jobs deletes this source's rows whose suite isn't one of the CI workflow's jobs.

Deployments are read for the policy's two release environments; one is stored once it has a
``success`` status, and is never changed after. Incidents are issues labelled ``incident``: open
means ongoing, closed means resolved, and an issue no longer labelled ``incident`` is no longer
one. Each collection deletes this source's deployments and incidents it didn't see, so a
deployment deleted on GitHub (or a removed label) disappears here too. Incidents are not work:
they never become issue rows.

Every row is stored with ``source = "github"`` and matched on its ``external_id``, so running
the collector again updates rows in place instead of adding new ones.

The collector only reads: it sends GET requests and never writes to GitHub.
``python -m sdlc.signals.github`` (or ``once``) collects once and prints the counts;
``run --every SECONDS`` collects, waits and collects again, forever. A failed run is logged and
the next one tries again; a spent rate limit waits until it resets.

After each collection the forecaster agent saves fresh forecasts (``sdlc.forecaster``), whatever
``ORCHESTRATOR_MODE`` says: it writes only its own tables and sends nothing to GitHub. Then,
when ``PLANNER_MODE`` is ``on``, the planner drafts options for slipping milestones
(``sdlc.plan_runner``); it writes only audit rows and is never given the GitHub client.
"""

import argparse
import logging
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.changes import classify_files, infer_module
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.github_client import GitHubClient, GitHubError, RateLimited, parse_time
from sdlc.tables import CIRun, Deployment, Engineer, Epic, Incident, Issue, PullRequest
from sdlc.tiers import load_policy

log = logging.getLogger("sdlc.signals.github")

CI_WORKFLOW = "ci.yml"
LINKED_ISSUE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s*#(\d+)", re.I)
SUITES = {
    "CRM API (lint + tests)": "api",
    "CRM web (tests + build)": "web",
    "Insights web (tests + build)": "insights-web",
    "Orchestrator (lint + tests)": "orchestrator",
    "Migrations on MySQL": "migrations",
}
SUITE_LENGTH = 40
EPIC_LENGTH = 80
SEVERITIES = ("sev1", "sev2", "sev3")
DEFAULT_SEVERITY = "sev2"
INCIDENT_AFTER_DEPLOY = timedelta(hours=24)  # an incident opened within this of a deploy is its


def labels_of(item: dict) -> dict[str, str]:
    """``key:value`` labels as a dict; a label without a colon maps to ``"true"``."""
    labels: dict[str, str] = {}
    for label in item.get("labels") or []:
        name = label["name"] if isinstance(label, dict) else str(label)
        key, colon, value = name.partition(":")
        labels[key.strip()] = value.strip() if colon else "true"
    return labels


def linked_issue_number(body: str | None) -> int | None:
    match = LINKED_ISSUE.search(body or "")
    return int(match.group(1)) if match else None


def suite_of(job_name: str) -> str:
    return SUITES.get(job_name, job_name[:SUITE_LENGTH])


def _days(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 86400, 1)


def _hours(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds() / 3600, 1)


def _points(value: str | None) -> int | None:
    return int(value) if value and value.isdigit() else None


def release_environments() -> tuple[str, str]:
    """The policy's release environments: production and its sign-off environment."""
    release = load_policy().release
    return release.environment, release.signoff_environment


@dataclass
class CIMemory:
    """What this process has stored from CI: the finished runs and their jobs' suites.

    It lives as long as the process, so a restart reads every finished run's jobs once again.
    """

    finished_runs: set[int] = field(default_factory=set)
    suites: set[str] = field(default_factory=set)


class Collector:
    source = "github"

    def __init__(self, db: Session, client: GitHubClient, memory: CIMemory | None = None):
        self.db = db
        self.client = client
        self.memory = memory if memory is not None else CIMemory()
        self._engineers: dict[str, Engineer] = {}
        self._finished_runs: set[int] = set()
        self._suites: set[str] = set()

    def run(self, environments: Sequence[str] | None = None) -> dict[str, int]:
        """Collect everything; ``environments`` defaults to the policy's release environments."""
        if environments is None:
            environments = release_environments()
        counts = {
            "epics": self.collect_milestones(),
            "issues": self.collect_issues(),
            "pull_requests": self.collect_pull_requests(),
            "ci_jobs": self.collect_ci(),
        }
        # Committed before the deployments, so a token that can't read them loses nothing else;
        # their error still fails the pass.
        self.db.commit()
        # Remembered only once committed, so a failed pass reads those runs again.
        self.memory.finished_runs |= self._finished_runs
        self.memory.suites |= self._suites
        counts["deployments"] = self.collect_deployments(environments)
        counts["incidents"] = self.collect_incidents()
        self.db.commit()
        return counts

    def _row(self, table, external_id: str):
        row = self.db.scalar(
            select(table).where(table.source == self.source, table.external_id == external_id)
        )
        if row is None:
            row = table(source=self.source, external_id=external_id)
            self.db.add(row)
        return row

    def _engineer(self, user: dict | None) -> Engineer | None:
        if not user or not user.get("login"):
            return None
        login = user["login"]
        if login not in self._engineers:
            engineer = self.db.scalar(
                select(Engineer).where(Engineer.source == self.source, Engineer.login == login)
            )
            if engineer is None:
                engineer = Engineer(login=login, name=login, source=self.source)
                self.db.add(engineer)
            self._engineers[login] = engineer
        return self._engineers[login]

    def collect_milestones(self) -> int:
        """Store or update every milestone, open and closed, as an epic."""
        count = 0
        for item in self.client.paginate("/repos/{repo}/milestones", state="all"):
            epic = self._row(Epic, f"milestone-{item['number']}")
            epic.number = item["number"]
            epic.name = item["title"][:EPIC_LENGTH]
            epic.state = item["state"]
            due_on = parse_time(item.get("due_on"))
            epic.due_on = due_on.date() if due_on else None
            epic.created_at = parse_time(item["created_at"])
            epic.closed_at = parse_time(item.get("closed_at"))
            count += 1
        self.db.flush()
        return count

    def collect_issues(self) -> int:
        count = 0
        for item in self.client.paginate("/repos/{repo}/issues", state="all"):
            if "pull_request" in item or "incident" in labels_of(item):
                continue
            self.collect_issue(item)
            count += 1
        self.db.flush()
        return count

    def collect_issue(self, item: dict) -> Issue:
        """Store or update one issue from its list entry, with its current dimension labels."""
        labels = labels_of(item)
        issue = self._row(Issue, f"issue-{item['number']}")
        issue.number = item["number"]
        issue.title = item["title"][:300]
        issue.module = labels.get("module")
        issue.type = labels.get("type", "feature")
        issue.priority = labels.get("priority")
        milestone = item.get("milestone")
        issue.epic = milestone["title"][:EPIC_LENGTH] if milestone else labels.get("epic")
        issue.estimate_points = _points(labels.get("points"))
        issue.state = item["state"]
        issue.created_at = parse_time(item["created_at"])
        issue.closed_at = parse_time(item.get("closed_at"))
        issue.actual_days = _days(issue.created_at, issue.closed_at) if issue.closed_at else None
        issue.assignee = self._engineer(item.get("assignee"))
        return issue

    def collect_pull_requests(self) -> int:
        count = 0
        for item in self.client.paginate("/repos/{repo}/pulls", state="all"):
            self.collect_pull_request(item)
            count += 1
        self.db.flush()
        return count

    def collect_pull_request(self, item: dict) -> PullRequest:
        """Store or update one pull request from its list entry, with its files and reviews."""
        number = item["number"]
        detail = self.client.get(f"/repos/{{repo}}/pulls/{number}")
        files = self.client.paginate(f"/repos/{{repo}}/pulls/{number}/files")
        reviews = self.client.paginate(f"/repos/{{repo}}/pulls/{number}/reviews")
        pr = self._pull_request(detail, files, reviews)
        self.db.flush()
        return pr

    def _pull_request(self, detail: dict, files: list[dict], reviews: list[dict]) -> PullRequest:
        labels = labels_of(detail)
        paths = [file["filename"] for file in files]
        facts = classify_files(paths)
        created_at = parse_time(detail["created_at"])
        submitted = sorted(
            moment
            for moment in (parse_time(review.get("submitted_at")) for review in reviews)
            if moment is not None
        )
        merged_at = parse_time(detail.get("merged_at"))
        issue_number = linked_issue_number(detail.get("body"))

        author = self._engineer(detail.get("user"))
        issue = (
            self.db.scalar(
                select(Issue).where(
                    Issue.source == self.source, Issue.external_id == f"issue-{issue_number}"
                )
            )
            if issue_number is not None
            else None
        )

        pr = self._row(PullRequest, f"pr-{detail['number']}")
        pr.number = detail["number"]
        pr.title = detail["title"][:300]
        pr.author = author
        pr.issue = issue
        pr.module = labels.get("module") or infer_module(paths)
        pr.files_changed = detail.get("changed_files", len(files))
        pr.additions = detail.get("additions", 0)
        pr.deletions = detail.get("deletions", 0)
        pr.touches_migration = facts.touches_migration
        pr.touches_governance = facts.touches_governance
        pr.test_files_changed = facts.test_files_changed
        pr.docs_only = facts.docs_only
        pr.modules_touched = facts.modules_touched
        pr.review_count = len(submitted)
        pr.first_review_hours = _hours(created_at, submitted[0]) if submitted else None
        pr.rework_commits = max(0, detail.get("commits", 1) - 1)
        pr.state = "merged" if merged_at else detail["state"]
        pr.created_at = created_at
        pr.merged_at = merged_at
        pr.merge_commit_sha = detail.get("merge_commit_sha") if merged_at else None
        pr.closed_at = parse_time(detail.get("closed_at"))
        pr.caused_incident = "caused-incident" in labels
        pr.reverted = detail["title"].lower().startswith("revert")
        return pr

    def collect_ci(self) -> int:
        """Store the CI workflow's finished jobs, then remove rows of jobs that aren't CI.

        A finished run whose jobs this process already stored gets no jobs request.
        """
        count = 0
        runs = self.client.paginate(
            f"/repos/{{repo}}/actions/workflows/{CI_WORKFLOW}/runs", key="workflow_runs"
        )
        for run in runs:
            if run["id"] in self.memory.finished_runs:
                continue
            jobs = self.client.paginate(
                f"/repos/{{repo}}/actions/runs/{run['id']}/jobs", key="jobs", filter="all"
            )
            pull_request = self._run_pull_request(run)
            for job in jobs:
                if job.get("status") != "completed" or not job.get("conclusion"):
                    continue
                self._ci_job(job, jobs, pull_request)
                self._suites.add(suite_of(job["name"]))
                count += 1
            if run.get("status") == "completed":
                self._finished_runs.add(run["id"])
        self.db.flush()
        if count:
            self._remove_other_suites(self._suites | self.memory.suites)
        return count

    def _remove_other_suites(self, suites: set[str]) -> None:
        """Delete this source's CI rows whose suite isn't one of the CI workflow's jobs.

        Rows other workflows' jobs left before the collector read only ``CI_WORKFLOW`` go; a
        pass that stored no CI job never calls it, so a failed listing deletes nothing.
        """
        self.db.execute(
            delete(CIRun).where(CIRun.source == self.source, CIRun.suite.not_in(suites))
        )
        self.db.flush()

    def _run_pull_request(self, run: dict) -> PullRequest | None:
        linked = run.get("pull_requests") or []
        if not linked:
            return None
        return self.db.scalar(
            select(PullRequest).where(
                PullRequest.source == self.source,
                PullRequest.external_id == f"pr-{linked[0]['number']}",
            )
        )

    def _ci_job(self, job: dict, jobs: list[dict], pull_request: PullRequest | None) -> None:
        started_at = parse_time(job.get("started_at"))
        completed_at = parse_time(job.get("completed_at"))
        attempt = job.get("run_attempt", 1)
        flaky = job["conclusion"] == "failure" and any(
            other["name"] == job["name"]
            and other.get("run_attempt", 1) > attempt
            and other.get("conclusion") == "success"
            for other in jobs
        )

        ci_run = self._row(CIRun, f"job-{job['id']}")
        ci_run.pull_request = pull_request
        ci_run.suite = suite_of(job["name"])
        ci_run.conclusion = job["conclusion"][:20]
        ci_run.flaky = flaky
        ci_run.started_at = started_at
        ci_run.duration_seconds = (
            int((completed_at - started_at).total_seconds()) if started_at and completed_at else 0
        )

    def collect_deployments(self, environments: Sequence[str]) -> int:
        """Store each successful deployment to ``environments`` once; delete the ones gone.

        A deployment without a ``success`` status yet is skipped and checked again next time.
        Returns how many of this source's deployments are stored after the pass.
        """
        seen: set[str] = set()
        count = 0
        for environment in environments:
            listed = self.client.paginate("/repos/{repo}/deployments", environment=environment)
            for item in listed:
                external_id = f"deployment-{item['id']}"
                seen.add(external_id)
                if self._stored(Deployment, external_id) is not None:
                    count += 1
                    continue
                statuses = self.client.get(f"/repos/{{repo}}/deployments/{item['id']}/statuses")
                success = next((s for s in statuses or [] if s.get("state") == "success"), None)
                if success is None:
                    continue
                self.db.add(
                    Deployment(
                        source=self.source,
                        external_id=external_id,
                        sha=item["sha"],
                        version=item["sha"][:7],
                        deployed_at=parse_time(success["created_at"]),
                        pr_count=0,
                        status="success",
                    )
                )
                count += 1
        self.db.flush()
        gone = list(
            self.db.scalars(
                select(Deployment.id).where(
                    Deployment.source == self.source, Deployment.external_id.not_in(seen)
                )
            )
        )
        if gone:
            self.db.execute(
                update(Incident).where(Incident.deployment_id.in_(gone)).values(deployment_id=None)
            )
            self.db.execute(delete(Deployment).where(Deployment.id.in_(gone)))
            self.db.flush()
        return count

    def collect_incidents(self) -> int:
        """Store or update every issue labelled ``incident``; delete the incidents gone."""
        deploys = list(
            self.db.execute(
                select(Deployment.id, Deployment.deployed_at).where(
                    Deployment.source == self.source, Deployment.status == "success"
                )
            )
        )
        seen: set[str] = set()
        listed = self.client.paginate("/repos/{repo}/issues", state="all", labels="incident")
        for item in listed:
            labels = labels_of(item)
            if "pull_request" in item or "incident" not in labels:
                continue
            incident = self._row(Incident, f"issue-{item['number']}")
            seen.add(incident.external_id)
            incident.title = item["title"][:200]
            incident.severity = next((s for s in labels if s in SEVERITIES), DEFAULT_SEVERITY)
            module = labels.get("module")
            incident.module = module[:30] if module else None
            incident.opened_at = parse_time(item["created_at"])
            incident.resolved_at = parse_time(item.get("closed_at"))
            before = [
                (deployed_at, deploy_id)
                for deploy_id, deployed_at in deploys
                if deployed_at <= incident.opened_at <= deployed_at + INCIDENT_AFTER_DEPLOY
            ]
            incident.deployment_id = max(before)[1] if before else None
        self.db.flush()
        gone = Incident.external_id.not_in(seen)
        self.db.execute(delete(Incident).where(Incident.source == self.source, gone))
        self.db.flush()
        return len(seen)

    def _stored(self, table, external_id: str):
        return self.db.scalar(
            select(table).where(table.source == self.source, table.external_id == external_id)
        )


def collect_once(
    engine: Engine,
    client: GitHubClient,
    memory: CIMemory | None = None,
    environments: Sequence[str] | None = None,
) -> dict[str, int]:
    """Collect, save fresh forecasts, then plan when ``PLANNER_MODE`` is on.

    ``memory`` carries the finished CI runs from one collection to the next. A failed forecast or
    plan is logged and leaves the counts.
    """
    if environments is None:
        environments = release_environments()
    with Session(engine) as db:
        counts = Collector(db, client, memory).run(environments)
    from sdlc.forecaster import ALWAYS_ON, ForecastRunner

    try:
        log.info("Forecasts: %s", ForecastRunner(ALWAYS_ON, engine=engine).poll_once())
    except Exception:
        log.exception("The forecaster failed; the next collection runs it again")
    if get_settings().planner_mode == "on":
        from sdlc.agents.planner import planner_llm
        from sdlc.plan_runner import PlannerRunner

        try:
            log.info("Plans: %s", PlannerRunner(planner_llm(), "on", engine=engine).poll_once())
        except Exception:
            log.exception("The planner failed; the next collection runs it again")
    return counts


def run_every(
    seconds: float,
    collect: Callable[[], dict[str, int]],
    *,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utcnow,
    runs: int | None = None,
) -> None:
    """Collect, wait ``seconds`` and collect again, forever unless ``runs`` is given.

    A failed run is logged and the next one runs as usual. When GitHub's rate limit is spent, the
    wait lasts until it resets instead.
    """
    done = 0
    while runs is None or done < runs:
        done += 1
        wait = seconds
        try:
            log.info("Collected: %s", collect())
        except RateLimited as limited:
            wait = max(1.0, (limited.until - now()).total_seconds())
            log.warning("GitHub's rate limit is spent; waiting until %s UTC", limited.until)
        except Exception:
            log.exception("The collection failed; the next one runs as usual")
        sleep(wait)


def main(
    argv: list[str] | None = None,
    engine: Engine | None = None,
    client: GitHubClient | None = None,
    sleep: Callable[[float], None] = time.sleep,
    now: Callable[[], datetime] = utcnow,
    runs: int | None = None,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.signals.github", description="Collect real work from GitHub."
    )
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("once", help="collect once and print the counts (the default)")
    every = commands.add_parser("run", help="collect on a schedule, forever")
    every.add_argument("--every", type=float, required=True, metavar="SECONDS")
    args = parser.parse_args(argv)

    try:
        client = client or GitHubClient()
    except GitHubError as error:
        sys.exit(str(error))
    engine = engine or get_engine()
    if args.command == "run":
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
        log.info("Collecting from %s every %ss", client.repo, args.every)
        memory = CIMemory()
        run_every(
            args.every,
            lambda: collect_once(engine, client, memory),
            sleep=sleep,
            now=now,
            runs=runs,
        )
        return 0
    try:
        counts = collect_once(engine, client)
    except GitHubError as error:
        sys.exit(str(error))
    summary = ", ".join(f"{kind}: {count}" for kind, count in counts.items())
    print(f"Collected from {client.repo}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
