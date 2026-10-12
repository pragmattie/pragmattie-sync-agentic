"""The test selector: which CI jobs a pull request would need, and why, one line per job.

It is a recommendation only: CI still runs every job, so each commit's real CI result later shows
whether skipping would have been safe. Plain rules on the changed paths, no model call, so the
same PR always gets the same answer. A tier whose policy runs the full suite, a commit the risk
agent couldn't score, a CI workflow change or a path no rule covers all select every job.

``flaky_suites`` reads the recent CI runs; everything else here is a pure function.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc.changes import is_docs_or_config, is_test_file
from sdlc.tables import CIRun
from sdlc.tiers import Policy

AGENT = "test_selector"
AGENT_VERSION = "v1"

# the jobs of .github/workflows/ci.yml, by the suite names the collector stores
CI_SUITES = ("api", "orchestrator", "migrations", "web", "insights-web")
SUITE_NAMES = {
    "api": "CRM API",
    "orchestrator": "Orchestrator",
    "migrations": "Migrations",
    "web": "CRM web",
    "insights-web": "Insights web",
}

# (pattern, suites for source files, suites for test files): the first match wins. Source under
# apps/api or orchestrator also selects migrations, which runs `alembic check` and the MySQL tests.
PATH_RULES = (
    (re.compile(r"^apps/api/migrations/"), ("api", "migrations"), ("api", "migrations")),
    (re.compile(r"^orchestrator/migrations/"), ("orchestrator", "migrations"), ("orchestrator",)),
    (re.compile(r"^apps/api/"), ("api", "migrations"), ("api",)),
    (re.compile(r"^orchestrator/"), ("orchestrator", "migrations"), ("orchestrator",)),
    (re.compile(r"^apps/crm-web/"), ("web",), ("web",)),
    (re.compile(r"^apps/insights-web/"), ("insights-web",), ("insights-web",)),
)
WORKFLOWS = ".github/workflows/"

EVERY_JOB = "every job runs"
NOT_ASSESSED = "the risk agent couldn't score this commit, so nothing is skipped"

FLAKY_LOOKBACK = timedelta(days=60)
FLAKY_MIN_RUNS = 20
FLAKY_RATE = 0.03

TESTS_START, TESTS_END = "<!-- tests -->", "<!-- /tests -->"
TESTS_HEADING = "**Tests** (the test selector's recommendation; CI still runs every job)"
_SECTION = re.compile(re.escape(TESTS_START) + r".*?" + re.escape(TESTS_END), re.S)


@dataclass(frozen=True)
class Flaky:
    suite: str
    rate: float
    runs: int
    simulated_runs: int


@dataclass(frozen=True)
class Selection:
    suites: tuple[str, ...]
    reasons: dict[str, str]
    full: str | None = None
    flaky: tuple[Flaky, ...] = field(default=())

    @property
    def skipped(self) -> tuple[str, ...]:
        return tuple(suite for suite in CI_SUITES if suite not in self.suites)

    def as_record(self) -> dict:
        return {
            "suites": list(self.suites),
            "skipped": list(self.skipped),
            "reasons": dict(self.reasons),
            "full": self.full,
            "flaky": [
                {
                    "suite": flag.suite,
                    "rate": flag.rate,
                    "runs": flag.runs,
                    "simulated_runs": flag.simulated_runs,
                }
                for flag in self.flaky
            ],
        }

    @classmethod
    def from_record(cls, record: dict) -> "Selection":
        """A selection read back from its audit row's output."""
        return cls(
            tuple(record["suites"]),
            dict(record["reasons"]),
            record.get("full"),
            tuple(Flaky(**flag) for flag in record.get("flaky") or []),
        )


def _rule_for(path: str) -> tuple[tuple[str, ...], tuple[str, ...]] | None:
    for pattern, source, tests in PATH_RULES:
        if pattern.match(path):
            return source, tests
    return None


def by_paths(paths: list[str]) -> tuple[dict[str, str], str | None]:
    """Each selected suite with the first reason for it, or every job and why, at the first path
    that needs it."""
    reasons: dict[str, str] = {}
    for path in paths:
        if path.startswith(WORKFLOWS):
            return {}, f"it changes the CI workflow ({path})"
        rule = _rule_for(path)
        if rule is None:
            if is_docs_or_config(path):
                continue
            return {}, f"no rule covers {path}"
        test = is_test_file(path)
        kind = "tests" if test else "code"
        for suite in rule[1] if test else rule[0]:
            reasons.setdefault(suite, f"{kind} changed in {path}")
    return reasons, None


def select_suites(
    paths: list[str], *, tier: str, policy: Policy, assessed: bool = True
) -> Selection:
    """The jobs this commit would need at the tier in force (floors and ``/tier`` included)."""
    reasons, full = by_paths(paths)
    tests = policy.tiers[tier].tests
    if not assessed:
        full = NOT_ASSESSED
    elif tests.startswith("full suite"):
        full = f"{tier} runs the full suite ({tests})"
    if full:
        return Selection(CI_SUITES, {suite: EVERY_JOB for suite in CI_SUITES}, full)
    suites = tuple(suite for suite in CI_SUITES if suite in reasons)
    return Selection(suites, {suite: reasons[suite] for suite in suites})


def flaky_suites(db: Session, now: datetime) -> list[Flaky]:
    """The CI jobs that failed and then passed on a re-run often enough, from any source."""
    query = select(CIRun.suite, CIRun.flaky, CIRun.source).where(
        CIRun.started_at >= now - FLAKY_LOOKBACK,
        CIRun.started_at <= now,
        CIRun.suite.in_(CI_SUITES),
    )
    counts = {suite: [0, 0, 0] for suite in CI_SUITES}  # runs, flaky, simulated
    for suite, flaky, source in db.execute(query):
        count = counts[suite]
        count[0] += 1
        count[1] += 1 if flaky else 0
        count[2] += 1 if source == "synthetic" else 0
    flags = []
    for suite, (runs, flaky, simulated) in counts.items():
        if runs >= FLAKY_MIN_RUNS and flaky / runs >= FLAKY_RATE:
            flags.append(Flaky(suite, round(flaky / runs, 3), runs, simulated))
    return flags


def with_flaky(selection: Selection, flaky: list[Flaky]) -> Selection:
    """The selection with the flags for the suites it would run, and no others."""
    kept = tuple(flag for flag in flaky if flag.suite in selection.suites)
    return Selection(selection.suites, selection.reasons, selection.full, kept)


def _names(suites: tuple[str, ...]) -> str:
    return ", ".join(SUITE_NAMES[suite] for suite in suites) or "none"


def _flaky_line(flag: Flaky) -> str:
    simulated = f", {flag.simulated_runs} of them simulated" if flag.simulated_runs else ""
    return (
        f"- Possibly flaky: {SUITE_NAMES[flag.suite]} failed and then passed on a re-run in "
        f"{flag.rate:.0%} of {flag.runs} recent runs{simulated}. "
        "Re-run it before assuming a real failure."
    )


def section_lines(selection: Selection) -> list[str]:
    """The risk comment's Tests section, between its markers."""
    lines = [TESTS_START, TESTS_HEADING]
    if selection.full:
        lines.append(f"- Would run every job: {selection.full}.")
    elif not selection.suites:
        lines.append("- Would run no jobs: only docs or config changed.")
    else:
        lines.append(f"- Would run: **{_names(selection.suites)}**")
        lines += [
            f"  - {SUITE_NAMES[suite]}: {selection.reasons[suite]}" for suite in selection.suites
        ]
        lines.append(f"- Would skip: {_names(selection.skipped)}")
    lines += [_flaky_line(flag) for flag in selection.flaky]
    lines.append(TESTS_END)
    return lines


def replace_section(body: str, selection: Selection) -> str:
    """Swap the first Tests section for this selection's; a body without one is unchanged."""
    section = "\n".join(section_lines(selection))
    return _SECTION.sub(lambda _: section, body, count=1)
