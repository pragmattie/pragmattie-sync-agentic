"""The risk score: a 0-100 rubric of ten signals, each keeping the points it added.

Every signal is a small pure function, so a score can always be explained. History signals look
only at what was known when the pull request was opened (``reference = pr.created_at``): an
incident counts only if it opened before then, and only pull requests merged before then count.
Scoring an old pull request to grade the rubric therefore can't peek at the answer.
"""

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc.tables import CIRun, Incident, PullRequest

MAX_POINTS = {
    "change_size": 20,
    "blast_radius": 10,
    "module_risk": 20,
    "schema_migration": 15,
    "author_record": 10,
    "tests_with_change": 10,
    "ci_signal": 10,
    "review_depth": 5,
    "timing": 5,
    "rework_churn": 5,
}
SHRINKAGE = 10  # a module's rate is pulled toward the team rate as if it had 10 more PRs
MIN_AUTHOR_PRS = 5
AUTHOR_WINDOW = 20


@dataclass(frozen=True)
class Features:
    lines: int
    files_changed: int
    modules_touched: int
    touches_migration: bool
    docs_only: bool
    test_files_changed: int
    review_count: int
    rework_commits: int
    real_ci_failures: int
    at: datetime
    module_rate: float
    max_module_rate: float
    author_ratio: float | None  # None: the author's record is unknown


@dataclass(frozen=True)
class Score:
    signals: dict[str, int]
    total: int


def change_size(lines: int) -> int:
    if lines < 50:
        return 0
    if lines < 150:
        return 5
    if lines < 400:
        return 10
    if lines < 800:
        return 15
    return 20


def blast_radius(files_changed: int, modules_touched: int) -> int:
    if files_changed < 3:
        points = 0
    elif files_changed < 6:
        points = 2
    elif files_changed < 11:
        points = 4
    elif files_changed < 21:
        points = 6
    else:
        points = 7
    if modules_touched > 2:
        points += 3
    return min(points, MAX_POINTS["blast_radius"])


def module_risk(module_rate: float, max_module_rate: float) -> int:
    if not max_module_rate:
        return 0
    return round(MAX_POINTS["module_risk"] * module_rate / max_module_rate)


def schema_migration(touches_migration: bool) -> int:
    return MAX_POINTS["schema_migration"] if touches_migration else 0


def author_record(author_ratio: float | None) -> int:
    if author_ratio is None:
        return 5
    return min(MAX_POINTS["author_record"], round(5 * author_ratio))


def tests_with_change(docs_only: bool, lines: int, test_files_changed: int) -> int:
    if not docs_only and lines >= 50 and test_files_changed == 0:
        return MAX_POINTS["tests_with_change"]
    return 0


def ci_signal(real_ci_failures: int) -> int:
    return {0: 0, 1: 4, 2: 7}.get(real_ci_failures, MAX_POINTS["ci_signal"])


def review_depth(review_count: int, lines: int) -> int:
    if review_count == 0 or (review_count == 1 and lines >= 400):
        return MAX_POINTS["review_depth"]
    return 0


def timing(at: datetime) -> int:
    """Friday, the weekend, or outside 08:00-18:00 (``at`` is naive UTC)."""
    if at.weekday() >= 4 or at.hour < 8 or at.hour >= 18:
        return MAX_POINTS["timing"]
    return 0


def rework_churn(rework_commits: int) -> int:
    return {0: 0, 1: 2, 2: 3}.get(rework_commits, MAX_POINTS["rework_churn"])


def score_features(features: Features) -> Score:
    f = features
    signals = {
        "change_size": change_size(f.lines),
        "blast_radius": blast_radius(f.files_changed, f.modules_touched),
        "module_risk": module_risk(f.module_rate, f.max_module_rate),
        "schema_migration": schema_migration(f.touches_migration),
        "author_record": author_record(f.author_ratio),
        "tests_with_change": tests_with_change(f.docs_only, f.lines, f.test_files_changed),
        "ci_signal": ci_signal(f.real_ci_failures),
        "review_depth": review_depth(f.review_count, f.lines),
        "timing": timing(f.at),
        "rework_churn": rework_churn(f.rework_commits),
    }
    return Score(signals=signals, total=min(100, sum(signals.values())))


def _merged_before(reference: datetime):
    return PullRequest.merged_at.is_not(None) & (PullRequest.merged_at < reference)


def _incident_before(reference: datetime):
    """True for a pull request that caused an incident opened before ``reference``."""
    return (
        select(Incident.id)
        .where(Incident.caused_by_pr_id == PullRequest.id, Incident.opened_at < reference)
        .exists()
    )


def module_rates(db: Session, reference: datetime) -> tuple[dict[str, float], float]:
    """Each module's shrunk incident rate, and the team rate, from PRs merged before ``reference``.

    A module's rate is (incident PRs + team rate x SHRINKAGE) / (merged PRs + SHRINKAGE).
    """
    merged = {
        module: int(count)
        for module, count in db.execute(
            select(PullRequest.module, func.count(PullRequest.id))
            .where(_merged_before(reference))
            .group_by(PullRequest.module)
        )
    }
    incidents = {
        module: int(count)
        for module, count in db.execute(
            select(PullRequest.module, func.count(PullRequest.id))
            .where(_merged_before(reference), _incident_before(reference))
            .group_by(PullRequest.module)
        )
    }
    total = sum(merged.values())
    team_rate = sum(incidents.values()) / total if total else 0.0
    rates = {
        module: (incidents.get(module, 0) + team_rate * SHRINKAGE) / (count + SHRINKAGE)
        for module, count in merged.items()
        if module is not None
    }
    return rates, team_rate


def author_ratio(
    db: Session, author_id: int | None, reference: datetime, team_rate: float
) -> float | None:
    """The author's incident share over their last 20 merged PRs, divided by the team rate."""
    if author_id is None or not team_rate:
        return None
    caused = db.scalars(
        select(_incident_before(reference))
        .where(PullRequest.author_id == author_id, _merged_before(reference))
        .order_by(PullRequest.merged_at.desc())
        .limit(AUTHOR_WINDOW)
    ).all()
    if len(caused) < MIN_AUTHOR_PRS:
        return None
    return sum(1 for flag in caused if flag) / len(caused) / team_rate


def compute_features(db: Session, pr: PullRequest, now: datetime | None = None) -> Features:
    reference = pr.created_at
    rates, team_rate = module_rates(db, reference)
    if pr.module is None:
        module_rate = 0.0
    else:
        module_rate = rates.get(pr.module, team_rate)
    failures = db.scalar(
        select(func.count(CIRun.id)).where(
            CIRun.pull_request_id == pr.id,
            CIRun.conclusion == "failure",
            CIRun.flaky.is_(False),
        )
    )
    return Features(
        lines=pr.additions + pr.deletions,
        files_changed=pr.files_changed,
        modules_touched=pr.modules_touched,
        touches_migration=pr.touches_migration,
        docs_only=pr.docs_only,
        test_files_changed=pr.test_files_changed,
        review_count=pr.review_count,
        rework_commits=pr.rework_commits,
        real_ci_failures=int(failures or 0),
        at=pr.merged_at or now or pr.created_at,
        module_rate=module_rate,
        max_module_rate=max(rates.values(), default=0.0),
        author_ratio=author_ratio(db, pr.author_id, reference, team_rate),
    )


def score_pull_request(db: Session, pr: PullRequest, now: datetime | None = None) -> Score:
    return score_features(compute_features(db, pr, now))


def features_digest(features: Features) -> dict:
    """The features as a JSON-safe dict, with ``at`` as ISO text."""
    digest = asdict(features)
    digest["at"] = features.at.isoformat()
    return digest
