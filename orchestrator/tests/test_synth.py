from datetime import datetime, timedelta
from statistics import mean

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from sdlc import synth
from sdlc.db import Base
from sdlc.tables import Engineer, Issue, PullRequest, Sprint

EVEN_WEEK_NOW = datetime(2026, 10, 2, 15, 30)  # ISO week 40, a Friday
ODD_WEEK_NOW = datetime(2026, 7, 15, 11, 0)  # ISO week 29, a Wednesday
FRIDAY = datetime(2026, 9, 18, 14, 0)
TUESDAY = datetime(2026, 9, 15, 14, 0)


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


@pytest.fixture
def built(session):
    synth.build(session, now=EVEN_WEEK_NOW)
    session.commit()
    return session


def _snapshot(session):
    def rows(table, *columns):
        return session.execute(select(*columns).order_by(table.id)).all()

    return (
        rows(Sprint, Sprint.name, Sprint.start_date, Sprint.end_date, Sprint.goal),
        rows(
            Issue,
            Issue.external_id,
            Issue.title,
            Issue.module,
            Issue.type,
            Issue.priority,
            Issue.estimate_points,
            Issue.actual_days,
            Issue.state,
            Issue.created_at,
            Issue.closed_at,
        ),
        rows(
            PullRequest,
            PullRequest.external_id,
            PullRequest.title,
            PullRequest.additions,
            PullRequest.deletions,
            PullRequest.files_changed,
            PullRequest.rework_commits,
            PullRequest.first_review_hours,
            PullRequest.state,
            PullRequest.created_at,
            PullRequest.merged_at,
        ),
    )


@pytest.mark.parametrize("now", [EVEN_WEEK_NOW, ODD_WEEK_NOW], ids=["even-week", "odd-week"])
def test_thirteen_sprints_start_on_even_week_mondays_and_the_last_contains_now(session, now):
    assert now.isocalendar().week % 2 == (0 if now is EVEN_WEEK_NOW else 1)
    counts = synth.build(session, now=now)

    sprints = session.scalars(select(Sprint).order_by(Sprint.start_date)).all()
    assert counts["sprints"] == len(sprints) == 13
    assert [sprint.name for sprint in sprints] == [f"Sprint {i}" for i in range(1, 14)]
    for sprint, following in zip(sprints, sprints[1:], strict=False):
        assert following.start_date - sprint.start_date == timedelta(days=14)
    for sprint in sprints:
        assert sprint.start_date.weekday() == 0
        assert sprint.start_date.isocalendar().week % 2 == 0
        assert sprint.end_date - sprint.start_date == timedelta(days=13)
        assert sprint.source == "synthetic"
    assert sprints[-1].start_date <= now.date() <= sprints[-1].end_date
    assert [sprint.goal for sprint in sprints if sprint.goal] == [
        "Release 2026.1",
        "Release 2026.2",
        "Release 2026.3",
    ]
    assert [i for i, sprint in enumerate(sprints) if sprint.goal] == [3, 7, 11]


def test_the_same_seed_and_now_give_identical_rows(tmp_path):
    snapshots = []
    for name, seed in (("a", 7), ("b", 7), ("c", 8)):
        engine = create_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            synth.build(session, now=EVEN_WEEK_NOW, seed=seed)
            snapshots.append(_snapshot(session))
        engine.dispose()

    assert snapshots[0] == snapshots[1]
    assert snapshots[0] != snapshots[2]


def test_every_row_is_synthetic_and_counts_match(session):
    counts = synth.build(session, now=EVEN_WEEK_NOW)

    for table in (Engineer, Sprint, Issue, PullRequest):
        sources = set(session.scalars(select(table.source)))
        assert sources == {"synthetic"}
    assert counts == {
        "engineers": 6,
        "sprints": 13,
        "issues": session.scalar(select(func.count(Issue.id))),
        "pull_requests": session.scalar(select(func.count(PullRequest.id))),
    }
    assert counts["issues"] > 200
    assert counts["pull_requests"] > counts["issues"]
    leo = session.scalars(select(Engineer).where(Engineer.login == "leo-m")).one()
    assert leo.role == "Product manager"
    assert session.scalar(select(func.count()).where(Issue.assignee_id == leo.id)) == 0


def test_nothing_is_dated_after_now_and_open_issues_have_no_actual_days(built):
    session = built
    issues = session.scalars(select(Issue)).all()
    for issue in issues:
        assert issue.created_at <= EVEN_WEEK_NOW
        if issue.state == "open":
            assert issue.actual_days is None
            assert issue.closed_at is None
        else:
            assert issue.state == "closed"
            assert issue.closed_at <= EVEN_WEEK_NOW
            assert issue.actual_days is not None
            assert issue.closed_at.weekday() < 5
    assert {issue.state for issue in issues} == {"open", "closed"}

    pull_requests = session.scalars(select(PullRequest)).all()
    for pr in pull_requests:
        assert pr.created_at <= EVEN_WEEK_NOW
        for moment in (pr.merged_at, pr.closed_at):
            assert moment is None or moment <= EVEN_WEEK_NOW
        assert (pr.merged_at is not None) == (pr.state == "merged")
    assert {pr.state for pr in pull_requests} == {"open", "merged", "closed"}


def test_issues_and_pull_requests_follow_the_spec_shapes(built):
    session = built
    issues = session.scalars(select(Issue).order_by(Issue.id)).all()
    assert [issue.number for issue in issues] == list(range(1, len(issues) + 1))
    assert all(issue.external_id == f"syn-issue-{issue.number}" for issue in issues)
    for issue in issues:
        verb, _ = issue.title.split(" ", 1)
        assert verb in synth.VERBS[issue.type]
        assert issue.estimate_points in synth.POINTS[issue.type]
        assert issue.assignee_id is not None and issue.sprint_id is not None

    pull_requests = session.scalars(select(PullRequest)).all()
    for pr in pull_requests:
        assert pr.external_id == f"syn-pr-{pr.number}"
        assert pr.additions >= 5 and pr.files_changed >= 1
        assert pr.test_files_changed <= pr.files_changed
        assert pr.author_id == pr.issue.assignee_id
        assert pr.title.startswith(pr.issue.title)
        if pr.docs_only:
            assert pr.test_files_changed == 0 and not pr.touches_migration
        if pr.issue.type == "chore":
            assert not pr.touches_migration


def test_forecasting_runs_well_over_estimate_compared_with_leads(built):
    def days_per_point(module):
        rows = built.execute(
            select(Issue.actual_days, Issue.estimate_points).where(
                Issue.module == module, Issue.actual_days.is_not(None)
            )
        ).all()
        return mean(days / points for days, points in rows)

    assert days_per_point("forecasting") > 1.3 * days_per_point("leads")


def test_tomas_averages_more_rework_than_marcus(built):
    def rework(login):
        return built.scalar(
            select(func.avg(PullRequest.rework_commits))
            .join(Engineer, PullRequest.author_id == Engineer.id)
            .where(Engineer.login == login)
        )

    assert rework("tomas-r") > rework("marcus-l") + 1


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (dict(), 0.01),
        (dict(module="billing_auth"), 0.04),
        (dict(author="marcus-l"), 0.005),
        (dict(author="tomas-r", module="forecasting"), 0.01 * 1.5 * 1.6),
        (dict(additions=251), 0.02),
        (dict(additions=501), 0.04),
        (dict(touches_migration=True), 0.03),
        (dict(merged_at=FRIDAY), 0.02),
        (
            dict(module="billing_auth", additions=800, touches_migration=True, merged_at=FRIDAY),
            0.01 * 4 * 4 * 3 * 2,
        ),
        (dict(module="unknown", author="someone"), 0.01),
        (dict(docs_only=True, module="billing_auth"), 0.0),
        (dict(merged_at=None, module="billing_auth"), 0.0),
    ],
)
def test_incident_risk_returns_the_documented_values(case, expected):
    arguments = dict(
        module="platform",
        author="priya-n",
        additions=100,
        touches_migration=False,
        docs_only=False,
        merged_at=TUESDAY,
    )
    assert synth.incident_risk(**{**arguments, **case}) == pytest.approx(expected)


def test_reset_leaves_no_synthetic_rows_and_keeps_github_rows(built):
    session = built
    session.add(
        PullRequest(external_id="7", source="github", title="Real change", created_at=TUESDAY)
    )
    session.commit()
    assert synth.has_synthetic(session)

    synth.reset(session)
    session.commit()

    assert not synth.has_synthetic(session)
    for table in (Engineer, Sprint, Issue, PullRequest):
        assert session.scalar(select(func.count()).where(table.source == "synthetic")) == 0
    assert session.scalars(select(PullRequest.title)).all() == ["Real change"]


def test_cli_refuses_a_second_build_without_reset(sqlite_engine, capsys):
    Base.metadata.create_all(sqlite_engine)

    assert synth.main([], engine=sqlite_engine) == 0
    assert "sprints: 13" in capsys.readouterr().out

    assert synth.main([], engine=sqlite_engine) == 1
    assert "--reset" in capsys.readouterr().err

    assert synth.main(["--if-empty"], engine=sqlite_engine) == 0
    assert "nothing to do" in capsys.readouterr().out

    assert synth.main(["--reset"], engine=sqlite_engine) == 0
    assert "sprints: 13" in capsys.readouterr().out
    with Session(sqlite_engine) as session:
        assert session.scalar(select(func.count(Sprint.id))) == 13
        assert session.scalar(select(func.count(Engineer.id))) == 6
