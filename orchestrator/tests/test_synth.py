import time
from collections import defaultdict
from datetime import datetime, timedelta
from statistics import mean
from unittest import mock

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from sdlc import synth
from sdlc.db import Base
from sdlc.tables import CIRun, Deployment, Engineer, Incident, Issue, PullRequest, Sprint

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


@pytest.mark.skipif(not hasattr(time, "tzset"), reason="time.tzset is Unix-only")
def test_builds_default_time_is_utc_whatever_the_local_zone(session, monkeypatch):
    from datetime import UTC

    monkeypatch.setenv("TZ", "America/Los_Angeles")
    time.tzset()
    try:
        with mock.patch.object(synth, "_Builder") as builder:
            synth.build(session)
        utc = datetime.now(UTC).replace(tzinfo=None)
    finally:
        monkeypatch.undo()
        time.tzset()
    now = builder.call_args.args[1]
    assert abs(now - utc) < timedelta(seconds=2)
    assert now.tzinfo is None
    assert now.microsecond == 0


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

    for table in (Engineer, Sprint, Issue, PullRequest, CIRun, Deployment, Incident):
        sources = set(session.scalars(select(table.source)))
        assert sources == {"synthetic"}
    assert counts == {
        "engineers": 6,
        "sprints": 13,
        "issues": session.scalar(select(func.count(Issue.id)).where(Issue.sprint_id.is_not(None))),
        "epic_backlog": session.scalar(
            select(func.count(Issue.id)).where(Issue.sprint_id.is_(None))
        ),
        "pull_requests": session.scalar(select(func.count(PullRequest.id))),
        "ci_runs": session.scalar(select(func.count(CIRun.id))),
        "deployments": session.scalar(select(func.count(Deployment.id))),
        "incidents": session.scalar(select(func.count(Incident.id))),
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
        if issue.sprint_id is None:
            assert issue.epic is not None and issue.assignee_id is None
        else:
            assert issue.assignee_id is not None

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


def _push(run):
    """The push number in ``syn-ci-<pr>-<push>-<suite>[-rerun]``."""
    return int(run.external_id.split("-")[3])


def _first_runs(session):
    """Every CI run except re-runs."""
    return session.scalars(
        select(CIRun).where(CIRun.external_id.not_like("%-rerun")).order_by(CIRun.id)
    ).all()


def test_every_pull_request_runs_all_suites_on_each_push(built):
    session = built
    pushes = defaultdict(lambda: defaultdict(set))
    for run in _first_runs(session):
        assert run.external_id == f"syn-ci-{run.pull_request.number}-{_push(run)}-{run.suite}"
        assert run.started_at <= EVEN_WEEK_NOW
        pushes[run.pull_request_id][_push(run)].add(run.suite)

    pull_requests = session.scalars(select(PullRequest)).all()
    assert {pr.state for pr in pull_requests} == {"open", "merged", "closed"}
    for pr in pull_requests:
        by_push = pushes[pr.id]
        # Push 0 starts when the PR opens; later pushes only appear once they have happened.
        assert 0 in by_push
        assert sorted(by_push) == list(range(len(by_push)))
        assert len(by_push) <= 1 + pr.rework_commits
        for suites in by_push.values():
            assert suites == set(synth.CI_SUITES)
    assert any(len(pushes[pr.id]) > 1 for pr in pull_requests)


def test_pushes_start_after_the_pr_opens_and_durations_track_the_suite(built):
    session = built
    starts = defaultdict(dict)
    for run in _first_runs(session):
        seconds = synth.CI_SUITES[run.suite]
        assert 0.8 * seconds - 1 <= run.duration_seconds <= 1.3 * seconds + 1
        starts[run.pull_request][_push(run)] = run.started_at
    for pr, by_push in starts.items():
        assert by_push[0] == pr.created_at
        for push, started_at in by_push.items():
            if push:
                gap_hours = (started_at - pr.created_at) / timedelta(hours=push)
                assert 1 - 1 / 3600 <= gap_hours <= 6 + 1 / 3600


def test_integrations_e2e_is_the_flaky_suite(built):
    runs = _first_runs(built)
    for suite in synth.CI_SUITES:
        suite_runs = [run for run in runs if run.suite == suite]
        rate = sum(run.flaky for run in suite_runs) / len(suite_runs)
        if suite == "integrations-e2e":
            assert 0.04 <= rate <= 0.12
        else:
            assert rate < 0.03
    for run in runs:
        assert run.conclusion in {"success", "failure"}
        if run.flaky:
            assert run.conclusion == "failure"


def test_every_flaky_failure_has_a_passing_rerun_twelve_minutes_later(built):
    session = built
    reruns = {
        run.external_id: run
        for run in session.scalars(select(CIRun).where(CIRun.external_id.like("%-rerun")))
    }
    flaky = [run for run in _first_runs(session) if run.flaky]
    assert flaky
    for run in flaky:
        rerun = reruns.pop(f"{run.external_id}-rerun")
        assert rerun.suite == run.suite
        assert rerun.pull_request_id == run.pull_request_id
        assert rerun.conclusion == "success" and not rerun.flaky
        assert rerun.started_at - run.started_at == timedelta(minutes=12)
    assert reruns == {}


def test_large_pull_requests_fail_more_often_on_their_first_push(built):
    session = built
    real_failures = defaultdict(list)
    for run in _first_runs(session):
        if _push(run) == 0:
            failed = run.conclusion == "failure" and not run.flaky
            real_failures[run.pull_request.additions > 400].append(failed)
    assert mean(real_failures[True]) > mean(real_failures[False])


def test_ci_runs_never_change_the_issues_and_pull_requests(tmp_path):
    snapshots = []
    for name, with_ci in (("with", True), ("without", False)):
        engine = create_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            if with_ci:
                synth.build(session, now=EVEN_WEEK_NOW)
                assert session.scalar(select(func.count(CIRun.id))) > 0
            else:
                with mock.patch.object(synth._Builder, "_ci_runs"):
                    synth.build(session, now=EVEN_WEEK_NOW)
                assert session.scalar(select(func.count(CIRun.id))) == 0
            snapshots.append(_snapshot(session))
        engine.dispose()

    assert snapshots[0] == snapshots[1]


def _merged(session):
    return session.scalars(
        select(PullRequest).where(PullRequest.state == "merged").order_by(PullRequest.merged_at)
    ).all()


def _risk(pr):
    return synth.incident_risk(
        module=pr.module,
        author=pr.author.login,
        additions=pr.additions,
        touches_migration=pr.touches_migration,
        docs_only=pr.docs_only,
        merged_at=pr.merged_at,
    )


def _deployments(session):
    return session.scalars(select(Deployment).order_by(Deployment.deployed_at)).all()


def test_about_three_percent_of_merged_prs_caused_incidents_mostly_risky_ones(built):
    merged = _merged(built)
    causes = [pr for pr in merged if pr.caused_incident]
    assert len(causes) == max(1, round(0.03 * len(merged)))
    assert mean(_risk(pr) for pr in causes) > 2 * mean(_risk(pr) for pr in merged)
    assert all(
        not pr.caused_incident for pr in built.scalars(select(PullRequest)) if not pr.merged_at
    )
    assert not any(pr.reverted for pr in merged if not pr.caused_incident)
    assert 0 < sum(pr.reverted for pr in causes) < len(causes)


def test_deploys_are_weekday_afternoons_up_to_now_and_ship_every_merged_pr_once(built):
    deployments = _deployments(built)
    assert len(deployments) > 50
    for number, deployment in enumerate(sorted(deployments, key=lambda d: d.id), start=1):
        deployed_at = deployment.deployed_at
        assert deployed_at.weekday() < 5
        assert deployed_at <= EVEN_WEEK_NOW
        assert 15 <= deployed_at.hour + deployed_at.minute / 60 <= 16
        day_of_year = deployed_at.timetuple().tm_yday
        assert deployment.version == f"{deployed_at.year}.{day_of_year:03d}.{number}"
        assert deployment.external_id == f"syn-deploy-{number}"

    # A PR ships in the first deploy after its merge, so each deploy carries exactly the PRs
    # merged since the one before it.
    merged = _merged(built)
    previous = datetime.min
    for deployment in deployments:
        shipped = [pr for pr in merged if previous <= pr.merged_at < deployment.deployed_at]
        assert deployment.pr_count == len(shipped) > 0
        previous = deployment.deployed_at
    last = deployments[-1].deployed_at
    assert sum(d.pr_count for d in deployments) == sum(pr.merged_at < last for pr in merged)


def _deployment_of(pr, deployments):
    return next(d for d in deployments if pr.merged_at < d.deployed_at)


def test_every_shipped_incident_cause_has_one_incident_linked_to_its_deploy(built):
    session = built
    deployments = _deployments(session)
    last = deployments[-1].deployed_at
    incidents = session.scalars(select(Incident)).all()
    by_pr = defaultdict(list)
    for incident in incidents:
        by_pr[incident.caused_by_pr_id].append(incident)

    shipped = [pr for pr in _merged(session) if pr.caused_incident and pr.merged_at < last]
    assert shipped
    for pr in shipped:
        deployment = _deployment_of(pr, deployments)
        found = by_pr.pop(pr.id, [])
        if deployment.deployed_at + timedelta(hours=20) <= EVEN_WEEK_NOW:
            assert len(found) == 1
        if not found:
            continue
        (incident,) = found
        assert incident.deployment_id == deployment.id
        assert incident.module == pr.module
        assert incident.external_id == f"syn-incident-{pr.number}"
        name = synth.MODULE_DISPLAY_NAMES[pr.module]
        assert incident.title == f"{name} degraded after {deployment.version}"
        if pr.module == "billing_auth":
            assert incident.severity == "sev1"
        else:
            assert incident.severity in {"sev2", "sev3"}

        opened_hours = (incident.opened_at - deployment.deployed_at) / timedelta(hours=1)
        assert 0.5 - 1 / 3600 <= opened_hours <= 20 + 1 / 3600
        assert incident.opened_at <= EVEN_WEEK_NOW
        if incident.resolved_at is not None:
            assert incident.resolved_at <= EVEN_WEEK_NOW
            low, high = (0.5, 3) if pr.reverted else (2, 14)
            hours = (incident.resolved_at - incident.opened_at) / timedelta(hours=1)
            assert low - 1 / 3600 <= hours <= high + 1 / 3600
        else:
            assert incident.opened_at + timedelta(hours=14) > EVEN_WEEK_NOW
    assert by_pr == {}


def test_a_reverted_cause_rolls_its_deploy_back(built):
    deployments = _deployments(built)
    merged = _merged(built)
    rolled_back = set()
    for pr in merged:
        if pr.caused_incident and pr.reverted and pr.merged_at < deployments[-1].deployed_at:
            rolled_back.add(_deployment_of(pr, deployments).id)
    assert rolled_back
    for deployment in deployments:
        expected = "rolled_back" if deployment.id in rolled_back else "success"
        assert deployment.status == expected


@pytest.mark.parametrize(
    "now",
    [datetime(2026, 10, 2, 15, 30), datetime(2026, 10, 3, 12, 0), datetime(2026, 8, 19, 9, 0)],
    ids=["friday-afternoon", "saturday", "wednesday-morning"],
)
def test_ongoing_incidents_have_no_resolution_and_nothing_is_after_now(session, now):
    synth.build(session, now=now)
    for deployment in session.scalars(select(Deployment)):
        assert deployment.deployed_at <= now and deployment.deployed_at.weekday() < 5
    for incident in session.scalars(select(Incident)):
        assert incident.opened_at <= now
        assert incident.resolved_at is None or incident.resolved_at <= now


def test_a_held_release_ships_later_with_the_work_that_waited(session):
    held_day = None

    def hold_first(deploy_at, pull_requests):
        nonlocal held_day
        if held_day is None:
            held_day = deploy_at.date()
        return deploy_at.date() != held_day

    with mock.patch.object(synth, "may_ship", side_effect=hold_first):
        synth.build(session, now=EVEN_WEEK_NOW)
    deployments = _deployments(session)
    assert deployments[0].deployed_at.date() > held_day
    merged = _merged(session)
    last = deployments[-1].deployed_at
    assert sum(d.pr_count for d in deployments) == sum(pr.merged_at < last for pr in merged)


def test_deploys_and_incidents_never_change_issues_prs_or_ci_runs(tmp_path):
    snapshots = []
    for name, with_deploys in (("with", True), ("without", False)):
        engine = create_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            if with_deploys:
                synth.build(session, now=EVEN_WEEK_NOW)
                assert session.scalar(select(func.count(Incident.id))) > 0
            else:
                with mock.patch.object(synth._Builder, "_deploys_and_incidents"):
                    synth.build(session, now=EVEN_WEEK_NOW)
                assert session.scalar(select(func.count(Deployment.id))) == 0
            ci_runs = session.execute(
                select(
                    CIRun.external_id,
                    CIRun.conclusion,
                    CIRun.flaky,
                    CIRun.started_at,
                    CIRun.duration_seconds,
                ).order_by(CIRun.id)
            ).all()
            snapshots.append((_snapshot(session), ci_runs))
        engine.dispose()

    assert snapshots[0] == snapshots[1]


def test_every_epic_has_open_unscheduled_stories_and_recent_closed_work(built):
    session = built
    today = EVEN_WEEK_NOW.date()
    for epic in synth.EPICS:
        issues = session.scalars(select(Issue).where(Issue.epic == epic.name)).all()
        backlog = [issue for issue in issues if issue.sprint_id is None]
        assert 4 <= len(backlog) <= 8
        for story in backlog:
            verb, work = story.title.split(" ", 1)
            assert verb in synth.VERBS["feature"] and work in epic.backlog
            assert story.module == epic.module and story.type == "feature"
            assert story.state == "open" and story.closed_at is None
            assert story.assignee_id is None and story.actual_days is None
            assert story.estimate_points in synth.EPIC_POINTS
            assert story.priority in synth.PRIORITIES
            assert story.created_at.time() == synth.EPIC_CREATED_TIME
            assert 3 <= (today - story.created_at.date()).days <= 40
        assert any(issue.state == "closed" and issue.sprint_id for issue in issues)


def test_only_recent_features_in_the_epic_module_are_tagged(built):
    session = built
    window_start = datetime.combine(synth.sprint_starts(EVEN_WEEK_NOW)[7], datetime.min.time())
    epics = {epic.module: epic.name for epic in synth.EPICS}
    eligible = tagged = 0
    for issue in session.scalars(select(Issue).where(Issue.sprint_id.is_not(None))):
        is_eligible = (
            issue.module in epics and issue.type == "feature" and issue.created_at >= window_start
        )
        if issue.epic is not None:
            assert is_eligible and issue.epic == epics[issue.module]
            tagged += 1
        eligible += is_eligible
    assert 0.65 <= tagged / eligible <= 0.95


def test_issue_numbers_stay_unique_with_the_epic_backlog(built):
    numbers = built.scalars(select(Issue.number).order_by(Issue.id)).all()
    assert numbers == list(range(1, len(numbers) + 1))
    backlog = built.scalars(select(Issue.number).where(Issue.sprint_id.is_(None))).all()
    assert min(backlog) > max(n for n in numbers if n not in backlog)


def test_epics_never_change_any_other_row(tmp_path):
    def rows(session, table, *columns):
        return session.execute(select(*columns).order_by(table.id)).all()

    snapshots = []
    for name, with_epics in (("with", True), ("without", False)):
        engine = create_engine(f"sqlite:///{tmp_path / f'{name}.db'}")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            if with_epics:
                synth.build(session, now=EVEN_WEEK_NOW)
                assert session.scalar(select(func.count()).where(Issue.epic.is_not(None))) > 0
            else:
                with mock.patch.object(synth._Builder, "_epics"):
                    synth.build(session, now=EVEN_WEEK_NOW)
            sprints, issues, pull_requests = _snapshot(session)
            scheduled = [
                row
                for row, sprint_id in zip(
                    issues, rows(session, Issue, Issue.sprint_id), strict=True
                )
                if sprint_id[0] is not None
            ]
            snapshots.append(
                (
                    sprints,
                    scheduled,
                    pull_requests,
                    rows(session, PullRequest, PullRequest.caused_incident, PullRequest.reverted),
                    rows(session, CIRun, CIRun.external_id, CIRun.conclusion, CIRun.started_at),
                    rows(
                        session,
                        Deployment,
                        Deployment.version,
                        Deployment.deployed_at,
                        Deployment.status,
                    ),
                    rows(session, Incident, Incident.external_id, Incident.opened_at),
                )
            )
        engine.dispose()

    assert snapshots[0] == snapshots[1]


def test_reset_leaves_no_synthetic_rows_and_keeps_github_rows(built):
    session = built
    session.add(
        PullRequest(external_id="7", source="github", title="Real change", created_at=TUESDAY)
    )
    session.commit()
    assert synth.has_synthetic(session)

    assert session.scalar(select(func.count(CIRun.id))) > 0
    assert session.scalar(select(func.count(Incident.id))) > 0

    synth.reset(session)
    session.commit()

    assert not synth.has_synthetic(session)
    for table in (Engineer, Sprint, Issue, PullRequest, CIRun, Deployment, Incident):
        assert session.scalar(select(func.count()).where(table.source == "synthetic")) == 0
    assert session.scalars(select(PullRequest.title)).all() == ["Real change"]


def test_reset_forgets_simulated_forecasts_and_keeps_real_ones_and_planner_rows(built):
    from datetime import date

    from sdlc import forecaster
    from sdlc.audit import record_decision
    from sdlc.tables import AgentDecision, Forecast

    session = built
    runner = forecaster.ForecastRunner("shadow", engine=session.get_bind(), runs=50)
    runner.poll_once(EVEN_WEEK_NOW)
    real = Forecast(
        created_at=TUESDAY,
        as_of=TUESDAY.date(),
        kind="epic",
        subject="M5 Delivery forecasting",
        source="github",
        trigger="schedule",
        inputs_hash="a" * 64,
        remaining_items=4,
        remaining_points=13,
        end_date=date(2026, 11, 20),
        throughput_mean=1.0,
        history_days=84,
        runs=50,
        seed=1,
    )
    session.add(real)
    session.flush()
    for agent, source, subject_id in (
        (forecaster.AGENT, "github", real.id),
        ("planner", "synthetic", 1),
    ):
        record_decision(
            session,
            agent=agent,
            agent_version="v1",
            subject_type="epic",
            subject_source=source,
            subject_id=subject_id,
            trigger="schedule",
        )
    session.commit()
    # The sprint and the four simulated epics.
    assert session.scalar(select(func.count()).where(Forecast.source == "synthetic")) == 5

    synth.reset(session)
    session.commit()

    assert session.scalars(select(Forecast.source)).all() == ["github"]
    kept = session.execute(select(AgentDecision.agent, AgentDecision.subject_source)).all()
    assert sorted(kept) == [("forecaster", "github"), ("planner", "synthetic")]


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
