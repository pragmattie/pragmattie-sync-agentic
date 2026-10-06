"""Real work from a GitHub repository: issues, pull requests with their files and reviews, CI jobs.

Every row is stored with ``source = "github"`` and matched on its ``external_id``, so running
the collector again updates rows in place instead of adding new ones.
"""

import re
import sys
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc.changes import classify_files, infer_module
from sdlc.db import get_engine
from sdlc.github_client import GitHubClient, GitHubError, parse_time
from sdlc.tables import CIRun, Engineer, Issue, PullRequest

DEPLOY_WORKFLOW = ".github/workflows/deploy.yml"
LINKED_ISSUE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\b:?\s*#(\d+)", re.I)
SUITES = {
    "CRM API (lint + tests)": "api",
    "CRM web (tests + build)": "web",
    "Insights web (tests + build)": "insights-web",
    "Orchestrator (lint + tests)": "orchestrator",
    "Migrations on MySQL": "migrations",
}
SUITE_LENGTH = 40


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


class Collector:
    source = "github"

    def __init__(self, db: Session, client: GitHubClient):
        self.db = db
        self.client = client
        self._engineers: dict[str, Engineer] = {}

    def run(self) -> dict[str, int]:
        counts = {
            "issues": self.collect_issues(),
            "pull_requests": self.collect_pull_requests(),
            "ci_jobs": self.collect_ci(),
        }
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

    def collect_issues(self) -> int:
        count = 0
        for item in self.client.paginate("/repos/{repo}/issues", state="all"):
            labels = labels_of(item)
            if "pull_request" in item or "incident" in labels:
                continue
            assignee = self._engineer(item.get("assignee"))
            issue = self._row(Issue, f"issue-{item['number']}")
            issue.number = item["number"]
            issue.title = item["title"][:300]
            issue.module = labels.get("module")
            issue.type = labels.get("type", "feature")
            issue.priority = labels.get("priority")
            issue.epic = labels.get("epic")
            issue.estimate_points = _points(labels.get("points"))
            issue.state = item["state"]
            issue.created_at = parse_time(item["created_at"])
            issue.closed_at = parse_time(item.get("closed_at"))
            issue.actual_days = (
                _days(issue.created_at, issue.closed_at) if issue.closed_at else None
            )
            issue.assignee = assignee
            count += 1
        self.db.flush()
        return count

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
        count = 0
        runs = self.client.paginate("/repos/{repo}/actions/runs", key="workflow_runs")
        for run in runs:
            if (run.get("path") or "").split("@")[0] == DEPLOY_WORKFLOW:
                continue
            jobs = self.client.paginate(
                f"/repos/{{repo}}/actions/runs/{run['id']}/jobs", key="jobs", filter="all"
            )
            pull_request = self._run_pull_request(run)
            for job in jobs:
                if job.get("status") != "completed" or not job.get("conclusion"):
                    continue
                self._ci_job(job, jobs, pull_request)
                count += 1
        self.db.flush()
        return count

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


def main(engine: Engine | None = None, client: GitHubClient | None = None) -> int:
    try:
        client = client or GitHubClient()
        with Session(engine or get_engine()) as db:
            counts = Collector(db, client).run()
    except GitHubError as error:
        sys.exit(str(error))
    summary = ", ".join(f"{kind}: {count}" for kind, count in counts.items())
    print(f"Collected from {client.repo}: {summary}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
