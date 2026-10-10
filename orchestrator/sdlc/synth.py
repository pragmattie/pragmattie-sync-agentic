"""Simulated engineering history: about six months of sprints, issues and pull requests.

Delivery Insights needs history to learn from before the real repository has much of its own.
Every row written here has ``source = "synthetic"``, so it can be told apart from (and removed
without touching) rows collected from GitHub.

Patterns built in on purpose, which the later models should rediscover:

- Forecasting and Integrations run 1.5-2x over estimate.
- Large Billing & Auth pull requests and migrations cause most incidents.
- Friday merges are riskier.
- Velocity dips in holiday sprints and in the sprint after a release.
- Marcus ships big pull requests with few defects, Dana small steady ones, and Tomas has slower
  reviews and more rework.

Merged work ships in near-daily releases of ``main``, and about 3% of merged pull requests (mostly
the risky ones) cause a production incident after their release.

Recent feature work belongs to four epics, and each epic still has unscheduled stories to do, so
every epic has a pace and a remaining backlog.

Determinism: the same ``seed`` and ``now`` always give the same rows. Sprints, issues and pull
requests draw from a single ``random.Random(seed)``. CI runs, deploys, incidents and later kinds
of history (epics) use their own streams, seeded from strings such as
``f"{seed}:ci:{pr_number}"``, and never draw from this one, so adding them never changes the rows
drawn here.

Run ``python -m sdlc.synth`` to add the history (``--if-empty`` to skip quietly when it exists,
``--reset`` to replace it).
"""

import argparse
import math
import random
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy import delete, exists, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import forecaster
from sdlc.clock import utcnow
from sdlc.db import get_engine
from sdlc.tables import CIRun, Deployment, Engineer, Incident, Issue, PullRequest, Sprint

SOURCE = "synthetic"


@dataclass(frozen=True)
class Coder:
    login: str
    name: str
    role: str
    share: float
    typical_size: int
    speed: float
    defect: float
    rework: float
    review_wait_hours: float


@dataclass(frozen=True)
class Epic:
    name: str
    module: str
    backlog: tuple[str, ...]


@dataclass(frozen=True)
class Module:
    name: str
    share: float
    overrun: tuple[float, float]
    risk: float
    migration_chance: float
    work_items: tuple[str, ...]


CODERS = (
    Coder("priya-n", "Priya N.", "Engineering manager", 0.08, 60, 1.0, 1.0, 0.5, 6),
    Coder("marcus-l", "Marcus L.", "Senior backend engineer", 0.30, 420, 0.85, 0.5, 0.6, 5),
    Coder("dana-k", "Dana K.", "Frontend engineer (Vue)", 0.27, 90, 1.0, 0.8, 0.8, 6),
    Coder("tomas-r", "Tomas R.", "Backend engineer", 0.22, 200, 1.3, 1.6, 2.5, 20),
    Coder("aisha-b", "Aisha B.", "QA / SDET", 0.13, 150, 1.0, 0.6, 0.7, 8),
)
PRODUCT_MANAGER = ("leo-m", "Leo M.", "Product manager")
CODERS_BY_LOGIN = {coder.login: coder for coder in CODERS}

MODULES = (
    Module(
        "leads", 0.16, (0.8, 1.2), 0.8, 0.10,
        ("lead scoring", "lead import", "duplicate detection", "lead assignment rules"),
    ),
    Module(
        "accounts", 0.12, (0.8, 1.2), 1.0, 0.15,
        ("account hierarchy", "contact roles", "account merge", "account timeline"),
    ),
    Module(
        "pipeline", 0.16, (0.9, 1.3), 1.2, 0.12,
        ("stage history", "deal board filters", "bulk stage update", "close-date alerts"),
    ),
    Module(
        "forecasting", 0.16, (1.5, 2.0), 1.5, 0.08,
        ("weighted forecast", "forecast snapshots", "quota tracking", "rollup math"),
    ),
    Module(
        "integrations", 0.14, (1.5, 2.0), 1.4, 0.05,
        ("email sync", "calendar sync", "webhook retries", "CSV export"),
    ),
    Module(
        "billing_auth", 0.10, (0.9, 1.4), 4.0, 0.30,
        ("SSO login", "seat billing", "plan upgrades", "role permissions"),
    ),
    Module(
        "platform", 0.16, (0.8, 1.2), 1.0, 0.05,
        ("API pagination", "audit logging", "search indexing", "job queue"),
    ),
)  # fmt: skip
MODULES_BY_NAME = {module.name: module for module in MODULES}

SPRINT_COUNT = 13
SPRINT_DAYS = 14
RELEASE_SPRINTS = (3, 7, 11)
BASE_CAPACITY = 75
HOLIDAYS = ((1, 1), (5, 25), (7, 4), (9, 7), (11, 26), (12, 25))

ISSUE_TYPES = ("feature", "bug", "chore")
ISSUE_TYPE_WEIGHTS = (60, 28, 12)
POINTS = {"feature": (1, 2, 3, 5, 8), "bug": (1, 2, 3), "chore": (1, 2, 3)}
VERBS = {"feature": ("Add", "Build", "Support"), "bug": ("Fix", "Resolve"), "chore": ("Refactor",)}
PRIORITIES = ("p1", "p2", "p3")
PRIORITY_WEIGHTS = (15, 55, 30)

# Typical seconds per CI suite. integrations-e2e is simulated only (no real CI job has that
# name) and is the flaky one.
CI_SUITES = {
    "api": 150,
    "web": 110,
    "migrations": 90,
    "orchestrator": 240,
    "integrations-e2e": 540,
}
FLAKY_SUITE = "integrations-e2e"
CI_RERUN_DELAY = timedelta(minutes=12)

EPICS = (
    Epic(
        "AI lead scoring", "leads",
        ("score explanations", "scoring model retraining", "score history", "score thresholds"),
    ),
    Epic(
        "Multi-currency forecasting", "forecasting",
        ("currency conversion rates", "per-currency quota", "FX rollup math", "FX snapshots"),
    ),
    Epic(
        "Salesforce import", "integrations",
        ("Salesforce field mapping", "Salesforce OAuth", "import dry run", "import conflicts"),
    ),
    Epic(
        "SOC 2 audit logging", "platform",
        ("audit event schema", "audit log retention", "audit log export", "admin access logs"),
    ),
)  # fmt: skip
EPIC_SPRINTS = 6
EPIC_TAG_CHANCE = 0.8
EPIC_BACKLOG_SIZE = (4, 8)
EPIC_POINTS = (2, 3, 3, 5, 5, 8)
EPIC_PRIORITY_WEIGHTS = (20, 60, 20)
EPIC_CREATED_TIME = time(10)

INCIDENT_SHARE = 0.03
DEPLOY_CHANCE = 0.75
DEPLOY_TIME = time(15)
MODULE_DISPLAY_NAMES = {
    "leads": "Leads",
    "accounts": "Accounts",
    "pipeline": "Pipeline",
    "forecasting": "Forecasting",
    "integrations": "Integrations",
    "billing_auth": "Billing & Auth",
    "platform": "Platform",
}


def incident_risk(
    *,
    module: str | None,
    author: str | None,
    additions: int,
    touches_migration: bool,
    docs_only: bool,
    merged_at: datetime | None,
) -> float:
    """Chance a merged pull request causes an incident (3.5 uses it to pick which ones did).

    Unknown modules and authors count as average (1.0).
    """
    if docs_only or merged_at is None:
        return 0.0
    module_risk = MODULES_BY_NAME[module].risk if module in MODULES_BY_NAME else 1.0
    defect = CODERS_BY_LOGIN[author].defect if author in CODERS_BY_LOGIN else 1.0
    risk = 0.01 * module_risk * defect
    if additions > 500:
        risk *= 4
    elif additions > 250:
        risk *= 2
    if touches_migration:
        risk *= 3
    if merged_at.weekday() == 4:
        risk *= 2
    return risk


def sprint_starts(now: datetime) -> list[date]:
    """Mondays of even ISO weeks, two weeks apart, the last one in the sprint containing ``now``."""
    monday = now.date() - timedelta(days=now.weekday())
    if monday.isocalendar().week % 2:
        monday -= timedelta(days=7)
    return [
        monday - timedelta(days=SPRINT_DAYS * (SPRINT_COUNT - 1 - i)) for i in range(SPRINT_COUNT)
    ]


def _has_holiday(start: date, end: date) -> bool:
    return any(
        start <= date(year, month, day) <= end
        for year in {start.year, end.year}
        for month, day in HOLIDAYS
    )


def _capacity(index: int, start: date, end: date) -> float:
    capacity = float(BASE_CAPACITY)
    if _has_holiday(start, end):
        capacity *= 0.7
    if index - 1 in RELEASE_SPRINTS:
        capacity *= 0.8
    return capacity


def _at(day: date, rng: random.Random, first_hour: int, last_hour: int) -> datetime:
    """A whole minute on ``day`` between ``first_hour``:00 and ``last_hour``:00."""
    minute = rng.randint(first_hour * 60, last_hour * 60)
    return datetime.combine(day, time()) + timedelta(minutes=minute)


def _after(start: datetime, hours: float) -> datetime:
    return (start + timedelta(hours=hours)).replace(microsecond=0)


def _past_weekend(moment: datetime) -> datetime:
    """Saturdays and Sundays move to the following Monday at 9:30."""
    if moment.weekday() < 5:
        return moment
    monday = moment.date() + timedelta(days=7 - moment.weekday())
    return datetime.combine(monday, time(9, 30))


def may_ship(deploy_at: datetime, pull_requests: list[PullRequest]) -> bool:
    """Whether a release of ``pull_requests`` may ship at ``deploy_at``.

    Always yes for now; the release gate (6.3) replaces this, and a hold only moves the work to a
    later deploy.
    """
    return True


def _pick(rng: random.Random, items, weights):
    return rng.choices(items, weights=weights)[0]


def _engineers(db: Session) -> dict[str, Engineer]:
    people = [(coder.login, coder.name, coder.role) for coder in CODERS] + [PRODUCT_MANAGER]
    existing = {
        engineer.login: engineer
        for engineer in db.scalars(select(Engineer).where(Engineer.source == SOURCE))
    }
    for login, name, role in people:
        if login not in existing:
            existing[login] = Engineer(login=login, name=name, role=role, source=SOURCE)
            db.add(existing[login])
    db.flush()
    return existing


class _Builder:
    def __init__(self, db: Session, now: datetime, seed: int):
        self.db = db
        self.now = now
        self.seed = seed
        self.rng = random.Random(seed)
        self.engineers = _engineers(db)
        self.issue_number = 0
        self.pr_number = 0
        self.issues: list[Issue] = []
        self.merged: list[tuple[PullRequest, float]] = []
        self.counts = {
            "engineers": len(self.engineers),
            "sprints": 0,
            "issues": 0,
            "epic_backlog": 0,
            "pull_requests": 0,
            "ci_runs": 0,
            "deployments": 0,
            "incidents": 0,
        }

    def build(self) -> dict[str, int]:
        for index, start in enumerate(sprint_starts(self.now)):
            self._sprint(index, start)
        self.db.flush()
        self._epics()
        self.db.flush()
        self._deploys_and_incidents()
        self.db.flush()
        return self.counts

    def _sprint(self, index: int, start: date) -> None:
        rng = self.rng
        end = start + timedelta(days=SPRINT_DAYS - 1)
        goal = f"Release 2026.{index // 4 + 1}" if index in RELEASE_SPRINTS else None
        sprint = Sprint(
            name=f"Sprint {index + 1}", start_date=start, end_date=end, goal=goal, source=SOURCE
        )
        self.db.add(sprint)
        self.db.flush()
        self.counts["sprints"] += 1

        target = _capacity(index, start, end) * rng.uniform(1.05, 1.3)
        planned = 0
        while planned < target:
            planned += self._issue(sprint)

    def _issue(self, sprint: Sprint) -> int:
        """Draws one issue for ``sprint`` and returns its points.

        An issue that would be created after ``now`` is drawn (so later draws stay the same) but
        not written.
        """
        rng = self.rng
        module = _pick(rng, MODULES, [m.share for m in MODULES])
        kind = _pick(rng, ISSUE_TYPES, ISSUE_TYPE_WEIGHTS)
        points = rng.choice(POINTS[kind])
        author = _pick(rng, CODERS, [c.share for c in CODERS])
        priority = _pick(rng, PRIORITIES, PRIORITY_WEIGHTS)
        title = f"{rng.choice(VERBS[kind])} {rng.choice(module.work_items)}"
        overrun = rng.uniform(*module.overrun)
        actual_days = round(points * 0.7 * overrun * author.speed * rng.uniform(0.8, 1.2), 1)

        work_day = sprint.start_date + timedelta(days=rng.randint(0, 6))
        work_start = _at(work_day, rng, 9, 12)
        created_at = _at(work_day - timedelta(days=rng.randint(1, 20)), rng, 9, 17)
        closed_at = _past_weekend(_after(work_start, actual_days * 24 * 1.4))
        if created_at > self.now:
            return points

        closed = closed_at <= self.now
        self.issue_number += 1
        issue = Issue(
            source=SOURCE,
            external_id=f"syn-issue-{self.issue_number}",
            number=self.issue_number,
            title=title,
            module=module.name,
            type=kind,
            priority=priority,
            estimate_points=points,
            actual_days=actual_days if closed else None,
            state="closed" if closed else "open",
            created_at=created_at,
            closed_at=closed_at if closed else None,
            sprint_id=sprint.id,
            assignee_id=self.engineers[author.login].id,
        )
        self.db.add(issue)
        self.db.flush()
        self.issues.append(issue)
        self.counts["issues"] += 1
        self._pull_requests(issue, module, author, work_day, actual_days)
        return points

    def _pull_requests(
        self, issue: Issue, module: Module, author: Coder, work_day: date, actual_days: float
    ) -> None:
        rng = self.rng
        points = issue.estimate_points
        if points == 1:
            parts = 1
        elif points <= 3:
            parts = rng.randint(1, 2)
        else:
            parts = rng.randint(2, 3)
        for k in range(parts):
            opened_at = _after(_at(work_day, rng, 10, 17), actual_days * (k + 0.5) / parts * 24)
            if opened_at > self.now:
                continue
            title = issue.title if parts == 1 else f"{issue.title} (part {k + 1} of {parts})"
            self._pull_request(issue, module, author, title, opened_at)

    def _pull_request(
        self, issue: Issue, module: Module, author: Coder, title: str, opened_at: datetime
    ) -> None:
        rng = self.rng
        size = rng.lognormvariate(math.log(author.typical_size), 0.7)
        if issue.type == "bug":
            size /= 3
        additions = max(5, round(size))
        deletions = int(additions * rng.uniform(0.1, 0.6))
        files_changed = max(1, additions // rng.randint(25, 60))
        touches_migration = issue.type != "chore" and rng.random() < module.migration_chance

        reviewer = rng.choice([coder for coder in CODERS if coder is not author])
        wait = reviewer.review_wait_hours
        first_review = round(max(0.5, rng.normalvariate(wait, wait / 3)), 1)
        rework = max(0, int(rng.normalvariate(author.rework, 1)))
        hours_to_merge = first_review + rework * rng.uniform(2, 8) + rng.uniform(1, 6)
        finished_at = _past_weekend(_after(opened_at, hours_to_merge))
        abandoned = rng.random() < 0.05

        docs_only = issue.type == "chore" and rng.random() < 0.6
        test_files = 0
        modules_touched = 1
        if not docs_only:
            test_chance = 0.85 if issue.type == "bug" else 0.7
            if rng.random() < test_chance:
                test_files = max(1, round(files_changed * rng.uniform(0.2, 0.5)))
            if files_changed >= 4 and rng.random() < 0.2:
                modules_touched += rng.randint(1, 2)
        touches_migration = touches_migration and not docs_only

        if finished_at > self.now:
            state = "open"
        else:
            state = "closed" if abandoned else "merged"
        reviewed = _after(opened_at, first_review) <= self.now
        if not reviewed:
            rework = 0

        self.pr_number += 1
        pr = PullRequest(
            source=SOURCE,
            external_id=f"syn-pr-{self.pr_number}",
            number=self.pr_number,
            title=title,
            author_id=self.engineers[author.login].id,
            issue_id=issue.id,
            module=module.name,
            files_changed=files_changed,
            additions=additions,
            deletions=deletions,
            touches_migration=touches_migration,
            test_files_changed=test_files,
            docs_only=docs_only,
            modules_touched=modules_touched,
            review_count=(1 + rework // 2 + (additions > 400)) if reviewed else 0,
            first_review_hours=first_review if reviewed else None,
            rework_commits=rework,
            state=state,
            created_at=opened_at,
            merged_at=finished_at if state == "merged" else None,
            closed_at=finished_at if state != "open" else None,
        )
        self.db.add(pr)
        self.counts["pull_requests"] += 1
        if state == "merged":
            risk = incident_risk(
                module=module.name,
                author=author.login,
                additions=additions,
                touches_migration=touches_migration,
                docs_only=docs_only,
                merged_at=pr.merged_at,
            )
            self.merged.append((pr, risk))
        self._ci_runs(pr)

    def _ci_runs(self, pr: PullRequest) -> None:
        """Every suite on every push, from the pull request's own stream.

        Runs that would start after ``now`` are drawn (so the rest stay the same) but not written.
        """
        rng = random.Random(f"{self.seed}:ci:{pr.number}")
        push_gap = rng.uniform(1, 6)
        for push in range(1 + pr.rework_commits):
            push_at = _after(pr.created_at, push * push_gap)
            fail_chance = 0.12 if push == 0 else 0.04
            if pr.additions > 400:
                fail_chance *= 1.5
            for suite, seconds in CI_SUITES.items():
                external_id = f"syn-ci-{pr.number}-{push}-{suite}"
                duration = round(seconds * rng.uniform(0.8, 1.3))
                flaky_chance = 0.08 if suite == FLAKY_SUITE else 0.01
                if rng.random() < fail_chance:
                    conclusion, flaky = "failure", False
                elif rng.random() < flaky_chance:
                    conclusion, flaky = "failure", True
                else:
                    conclusion, flaky = "success", False
                self._ci_run(pr, external_id, suite, conclusion, flaky, push_at, duration)
                if flaky:
                    rerun_duration = round(seconds * rng.uniform(0.8, 1.3))
                    self._ci_run(
                        pr,
                        f"{external_id}-rerun",
                        suite,
                        "success",
                        False,
                        push_at + CI_RERUN_DELAY,
                        rerun_duration,
                    )

    def _ci_run(
        self,
        pr: PullRequest,
        external_id: str,
        suite: str,
        conclusion: str,
        flaky: bool,
        started_at: datetime,
        duration: int,
    ) -> None:
        if started_at > self.now:
            return
        self.db.add(
            CIRun(
                source=SOURCE,
                external_id=external_id,
                pull_request=pr,
                suite=suite,
                conclusion=conclusion,
                flaky=flaky,
                started_at=started_at,
                duration_seconds=duration,
            )
        )
        self.counts["ci_runs"] += 1

    def _epics(self) -> None:
        """Tags recent feature work with its epic and adds each epic's unscheduled stories.

        Draws from its own stream, so the epics never change any other row.
        """
        rng = random.Random(f"{self.seed}:epics")
        window_start = datetime.combine(sprint_starts(self.now)[-EPIC_SPRINTS], time())
        epics_by_module = {epic.module: epic for epic in EPICS}
        for issue in self.issues:
            epic = epics_by_module.get(issue.module)
            if epic is None or issue.type != "feature" or issue.created_at < window_start:
                continue
            if rng.random() < EPIC_TAG_CHANCE:
                issue.epic = epic.name
        for epic in EPICS:
            for _ in range(rng.randint(*EPIC_BACKLOG_SIZE)):
                self._epic_story(epic, rng)

    def _epic_story(self, epic: Epic, rng: random.Random) -> None:
        title = f"{rng.choice(VERBS['feature'])} {rng.choice(epic.backlog)}"
        points = rng.choice(EPIC_POINTS)
        priority = _pick(rng, PRIORITIES, EPIC_PRIORITY_WEIGHTS)
        created_on = self.now.date() - timedelta(days=rng.randint(3, 40))
        self.issue_number += 1
        self.db.add(
            Issue(
                source=SOURCE,
                external_id=f"syn-issue-{self.issue_number}",
                number=self.issue_number,
                title=title,
                module=epic.module,
                type="feature",
                priority=priority,
                estimate_points=points,
                state="open",
                created_at=datetime.combine(created_on, EPIC_CREATED_TIME),
                epic=epic.name,
            )
        )
        self.counts["epic_backlog"] += 1

    def _deploys_and_incidents(self) -> None:
        self._pick_incident_causes()
        self._deploys()

    def _pick_incident_causes(self) -> None:
        """About 3% of merged PRs (at least one), drawn by incident risk squared, cause incidents.

        Half of them, by a 50% draw each, were reverted.
        """
        rng = random.Random(f"{self.seed}:incidents")
        candidates = [(pr, risk**2) for pr, risk in self.merged if risk > 0]
        count = max(1, round(INCIDENT_SHARE * len(self.merged)))
        causes = []
        while candidates and len(causes) < count:
            index = rng.choices(range(len(candidates)), weights=[w for _, w in candidates])[0]
            causes.append(candidates.pop(index)[0])
        for pr in causes:
            pr.caused_incident = True
            pr.reverted = rng.random() < 0.5

    def _deploys(self) -> None:
        """Walks day by day from the first merge to today, releasing ``main`` on most weekdays."""
        waiting = sorted((pr for pr, _ in self.merged), key=lambda pr: (pr.merged_at, pr.number))
        if not waiting:
            return
        day = waiting[0].merged_at.date()
        while day <= self.now.date() and waiting:
            rng = random.Random(f"{self.seed}:deploy:{day.isoformat()}")
            deploy_at = _after(datetime.combine(day, DEPLOY_TIME), rng.uniform(0, 1))
            wants = rng.random() < DEPLOY_CHANCE
            ready = [pr for pr in waiting if pr.merged_at < deploy_at]
            if (
                day.weekday() < 5
                and wants
                and deploy_at <= self.now
                and ready
                and may_ship(deploy_at, ready)
            ):
                waiting = waiting[len(ready) :]
                self._deploy(day, deploy_at, ready)
            day += timedelta(days=1)

    def _deploy(self, day: date, deploy_at: datetime, prs: list[PullRequest]) -> None:
        self.counts["deployments"] += 1
        number = self.counts["deployments"]
        deployment = Deployment(
            source=SOURCE,
            external_id=f"syn-deploy-{number}",
            version=f"{day.year}.{day.timetuple().tm_yday:03d}.{number}",
            deployed_at=deploy_at,
            pr_count=len(prs),
            status=(
                "rolled_back"
                if any(pr.caused_incident and pr.reverted for pr in prs)
                else "success"
            ),
        )
        self.db.add(deployment)
        for pr in sorted(prs, key=lambda pr: pr.number):
            if pr.caused_incident:
                self._incident(pr, deployment)

    def _incident(self, pr: PullRequest, deployment: Deployment) -> None:
        """The incident ``pr`` caused after ``deployment``, from the PR's own stream.

        An incident that would open after ``now`` is not written yet.
        """
        rng = random.Random(f"{self.seed}:incident:{pr.number}")
        severity = rng.choice(("sev2", "sev3", "sev3"))
        if pr.module == "billing_auth":
            severity = "sev1"
        opened_at = _after(deployment.deployed_at, rng.uniform(0.5, 20))
        restore_hours = rng.uniform(0.5, 3) if pr.reverted else rng.uniform(2, 14)
        resolved_at = _after(opened_at, restore_hours)
        if opened_at > self.now:
            return
        module = MODULE_DISPLAY_NAMES[pr.module]
        self.db.add(
            Incident(
                source=SOURCE,
                external_id=f"syn-incident-{pr.number}",
                title=f"{module} degraded after {deployment.version}",
                severity=severity,
                module=pr.module,
                opened_at=opened_at,
                resolved_at=resolved_at if resolved_at <= self.now else None,
                caused_by_pr=pr,
                deployment=deployment,
            )
        )
        self.counts["incidents"] += 1


def build(db: Session, now: datetime | None = None, seed: int = 7) -> dict[str, int]:
    """Generates the history up to ``now`` and returns how many rows of each kind it wrote."""
    now = (now or utcnow()).replace(microsecond=0)
    return _Builder(db, now, seed).build()


def reset(db: Session) -> None:
    """Deletes every synthetic row, children first, and the forecasts made from them."""
    forecaster.reset(db)
    synthetic_prs = select(PullRequest.id).where(PullRequest.source == SOURCE)
    synthetic_issues = select(Issue.id).where(Issue.source == SOURCE)
    synthetic_sprints = select(Sprint.id).where(Sprint.source == SOURCE)
    synthetic_engineers = select(Engineer.id).where(Engineer.source == SOURCE)
    synthetic_deployments = select(Deployment.id).where(Deployment.source == SOURCE)

    db.execute(delete(Incident).where(Incident.source == SOURCE))
    db.execute(
        update(Incident)
        .where(Incident.caused_by_pr_id.in_(synthetic_prs))
        .values(caused_by_pr_id=None)
    )
    db.execute(
        update(Incident)
        .where(Incident.deployment_id.in_(synthetic_deployments))
        .values(deployment_id=None)
    )
    for table in (CIRun, Deployment):
        db.execute(delete(table).where(table.source == SOURCE))
    db.execute(
        update(CIRun).where(CIRun.pull_request_id.in_(synthetic_prs)).values(pull_request_id=None)
    )
    db.execute(
        update(PullRequest).where(PullRequest.issue_id.in_(synthetic_issues)).values(issue_id=None)
    )
    db.execute(delete(PullRequest).where(PullRequest.source == SOURCE))
    db.execute(update(Issue).where(Issue.sprint_id.in_(synthetic_sprints)).values(sprint_id=None))
    db.execute(delete(Issue).where(Issue.source == SOURCE))
    db.execute(delete(Sprint).where(Sprint.source == SOURCE))
    db.execute(
        update(Issue).where(Issue.assignee_id.in_(synthetic_engineers)).values(assignee_id=None)
    )
    db.execute(
        update(PullRequest)
        .where(PullRequest.author_id.in_(synthetic_engineers))
        .values(author_id=None)
    )
    db.execute(delete(Engineer).where(Engineer.source == SOURCE))
    db.flush()


def has_synthetic(db: Session) -> bool:
    return bool(db.scalar(select(exists().where(Sprint.source == SOURCE))))


def main(argv: list[str] | None = None, engine: Engine | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.synth", description="Add simulated engineering history."
    )
    choice = parser.add_mutually_exclusive_group()
    choice.add_argument(
        "--if-empty", action="store_true", help="skip quietly if history already exists"
    )
    choice.add_argument("--reset", action="store_true", help="replace any existing history")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    with Session(engine or get_engine()) as db:
        if has_synthetic(db):
            if args.if_empty:
                print("Simulated history already exists; nothing to do.")
                return 0
            if not args.reset:
                print(
                    "Simulated history already exists. Use --reset to replace it, "
                    "or --if-empty to skip.",
                    file=sys.stderr,
                )
                return 1
            reset(db)
        counts = build(db, seed=args.seed)
        db.commit()
    print(", ".join(f"{kind}: {count}" for kind, count in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
