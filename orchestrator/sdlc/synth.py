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

Determinism: the same ``seed`` and ``now`` always give the same rows. Sprints, issues and pull
requests draw from a single ``random.Random(seed)``. CI runs and later kinds of history (deploys,
epics) use their own streams, seeded from strings such as ``f"{seed}:ci:{pr_number}"``, and
never draw from this one, so adding them never changes the rows drawn here.

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
        self.counts = {
            "engineers": len(self.engineers),
            "sprints": 0,
            "issues": 0,
            "pull_requests": 0,
            "ci_runs": 0,
        }

    def build(self) -> dict[str, int]:
        for index, start in enumerate(sprint_starts(self.now)):
            self._sprint(index, start)
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


def build(db: Session, now: datetime | None = None, seed: int = 7) -> dict[str, int]:
    """Generates the history up to ``now`` and returns how many rows of each kind it wrote."""
    now = (now or datetime.now()).replace(microsecond=0)
    return _Builder(db, now, seed).build()


def reset(db: Session) -> None:
    """Deletes every synthetic row, children first."""
    synthetic_prs = select(PullRequest.id).where(PullRequest.source == SOURCE)
    synthetic_issues = select(Issue.id).where(Issue.source == SOURCE)
    synthetic_sprints = select(Sprint.id).where(Sprint.source == SOURCE)
    synthetic_engineers = select(Engineer.id).where(Engineer.source == SOURCE)

    for table in (Incident, CIRun, Deployment):
        db.execute(delete(table).where(table.source == SOURCE))
    db.execute(
        update(Incident)
        .where(Incident.caused_by_pr_id.in_(synthetic_prs))
        .values(caused_by_pr_id=None)
    )
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
