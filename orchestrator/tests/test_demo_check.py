import shutil
from datetime import date, datetime

import httpx
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from sdlc import demo_check, demo_reset, synth
from sdlc.db import Base
from sdlc.demo_check import FAIL, PASS, WARN, Check
from sdlc.tables import Forecast
from tests.fakes import REPO, FakeGitHub

TODAY = date(2026, 10, 12)
NOW = datetime(2026, 10, 12, 9, 0)
ORCH_HEAD = demo_check.script_head(demo_check.ORCH_MIGRATIONS)
CRM_HEAD = demo_check.script_head(demo_check.CRM_MIGRATIONS)
ENVIRONMENTS = ("production", "production-signoff")
HEALTHY = {"status": "ok", "service": "x", "database": "ok"}


def _versions(engine, orchestrator=ORCH_HEAD, crm=CRM_HEAD) -> None:
    with engine.begin() as connection:
        for table, version in (("sdlc_alembic_version", orchestrator), ("alembic_version", crm)):
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
            connection.execute(text(f"CREATE TABLE {table} (version_num VARCHAR(32) NOT NULL)"))
            connection.execute(text(f"INSERT INTO {table} VALUES ('{version}')"))


def _crm(engine, accounts=20, leads=50, opportunities=50) -> None:
    with engine.begin() as connection:
        for table, count in (
            ("accounts", accounts),
            ("leads", leads),
            ("opportunities", opportunities),
        ):
            connection.execute(text(f"DROP TABLE IF EXISTS {table}"))
            connection.execute(text(f"CREATE TABLE {table} (id INTEGER PRIMARY KEY)"))
            for _ in range(count):
                connection.execute(text(f"INSERT INTO {table} DEFAULT VALUES"))


def _forecast(db: Session, as_of: date) -> None:
    db.add(
        Forecast(
            created_at=NOW,
            as_of=as_of,
            kind="sprint",
            subject="Sprint 13",
            source="synthetic",
            trigger="schedule",
            inputs_hash="0" * 64,
            remaining_items=3,
            remaining_points=8,
            throughput_mean=1.2,
            history_days=84,
            runs=100,
            seed=1,
        )
    )


@pytest.fixture(scope="module")
def ready_database(tmp_path_factory):
    """A database that is ready: both histories at head, CRM data, history and a forecast."""
    path = tmp_path_factory.mktemp("ready") / "ready.db"
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    _versions(engine)
    _crm(engine)
    with Session(engine) as db:
        synth.build(db, now=NOW)
        _forecast(db, TODAY)
        db.commit()
    engine.dispose()
    return path


@pytest.fixture
def engine(ready_database, tmp_path):
    """A copy of the ready database each test can change."""
    path = tmp_path / "test.db"
    shutil.copy(ready_database, path)
    engine = create_engine(f"sqlite:///{path}")
    yield engine
    engine.dispose()


def _repository() -> FakeGitHub:
    """A repository ready for a demo: labels, the deploy workflow, both environments."""
    gh = FakeGitHub()
    gh.repo_labels = {"demo": "D4C5F9", "incident": "D93F0B"}
    gh.contents.add(".github/workflows/deploy.yml")
    gh.add_environment("production")
    gh.add_environment("production-signoff", reviewer=True)
    gh.add_issue(1, "Score leads by activity")
    gh.add_issue(2, "Merge duplicate accounts")
    gh.add_pr(10, "sha10")
    gh.merge(10, datetime(2026, 10, 9, 12, 0), sha="merge10")
    return gh


@pytest.fixture
def gh():
    return _repository()


@pytest.fixture
def baseline(gh, tmp_path):
    path = tmp_path / "baseline.json"
    demo_reset.save_baseline(demo_reset.snapshot(gh), path)
    gh.requests.clear()
    return path


def _http(health: dict[str, object] | None = None) -> httpx.Client:
    """Both APIs answer with ``health[url]``: a dict as JSON, or an exception to raise."""
    answers = health or {}

    def handle(request: httpx.Request) -> httpx.Response:
        answer = answers.get(str(request.url), HEALTHY)
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(200, json=answer)

    return httpx.Client(transport=httpx.MockTransport(handle))


def _options(engine, gh, baseline) -> dict:
    return {
        "engine": engine,
        "gh": gh,
        "http": _http(),
        "mode": "enforce",
        "baseline_path": baseline,
        "environments": ENVIRONMENTS,
    }


def _run(engine, gh, baseline, smoke=False, **kwargs) -> list[Check]:
    options = {**_options(engine, gh, baseline), **kwargs}
    return demo_check.run_checks(smoke, TODAY, **options)


def _by_name(checks: list[Check]) -> dict[str, Check]:
    return {check.name: check for check in checks}


def test_a_ready_setup_passes_every_check_in_order(engine, gh, baseline):
    checks = _run(engine, gh, baseline)

    assert [(check.name, check.state) for check in checks] == [
        ("Database", PASS),
        ("Orchestrator migrations", PASS),
        ("CRM migrations", PASS),
        ("CRM demo data", PASS),
        ("Simulated history", PASS),
        ("Forecasts", PASS),
        ("Orchestrator API", PASS),
        ("CRM API", PASS),
        ("GitHub", PASS),
        ("Demo baseline", PASS),
        ("Label demo", PASS),
        ("Label incident", PASS),
        ("Deploy workflow", PASS),
        ("Environment production", PASS),
        ("Environment production-signoff", PASS),
        ("Token: deployments", PASS),
        ("Clean start", PASS),
        ("ORCHESTRATOR_MODE", PASS),
    ]
    found = _by_name(checks)
    assert found["Orchestrator migrations"].detail == f"at {ORCH_HEAD}"
    assert found["CRM migrations"].detail == f"at {CRM_HEAD}"
    assert found["CRM demo data"].detail == "20 accounts, 50 leads, 50 opportunities"
    assert found["Simulated history"].detail.endswith(" runs to Sun Oct 25")
    assert found["Forecasts"].detail == "1 saved today"
    assert found["GitHub"].detail == f"token works for {REPO}"
    assert found["Label demo"].detail == "exists (marks what the reset cleans up)"
    assert found["Label incident"].detail == "exists (raises a real incident)"
    assert found["Deploy workflow"].detail == "deploy.yml is on the default branch"
    assert found["Token: deployments"].detail == (
        "can read deployments (the reset also needs write, which can't be tested safely)"
    )
    assert found["Clean start"].detail == "the repository matches the demo baseline"
    assert gh.writes() == []


@pytest.mark.parametrize(
    ("mode", "state", "start"),
    [
        ("off", FAIL, "off: the agents won't react during the demo. Set it to shadow in .env."),
        ("shadow", WARN, "shadow: the release gate will only say what it would do"),
        (
            "observe",
            WARN,
            "observe: v2 decides but posts nothing; v1 still governs this repository until "
            "cutover (7.5).",
        ),
        ("enforce", PASS, "enforce: the release gate holds deploys and risk-gate shows"),
    ],
)
def test_the_mode_check(mode, state, start):
    check = demo_check.mode_check(mode)

    assert check.name == "ORCHESTRATOR_MODE"
    assert check.state == state
    assert check.detail.startswith(start)


def test_the_summary_line_and_a_warn_alone_is_ready():
    failed = Check("Forecasts", FAIL, "none")
    warned = Check("ORCHESTRATOR_MODE", WARN, "shadow")
    passed = Check("Database", PASS, "reachable")

    assert demo_check.summary([passed, failed, warned]) == "Not ready: 1 check(s) failed."
    assert demo_check.summary([passed, warned]) == "Ready for the demo."
    assert demo_check.summary([]) == "Ready for the demo."


def test_each_line_is_aligned():
    assert demo_check.line(Check("Database", PASS, "reachable")) == (
        f"  PASS  {'Database':<24} reachable"
    )


def test_main_prints_each_check_and_exits_1_on_a_failure(engine, gh, baseline, capsys, monkeypatch):
    monkeypatch.setattr(demo_check, "utcnow", lambda: NOW)
    options = {"engine": engine, "gh": gh, "http": _http(), "baseline_path": baseline}
    options["environments"] = ENVIRONMENTS

    assert demo_check.main([], mode="shadow", **options) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "  PASS  Database                 reachable"
    assert lines[-2].startswith("  WARN  ORCHESTRATOR_MODE        shadow:")
    assert lines[-1] == "Ready for the demo."

    assert demo_check.main([], mode="off", **options) == 1
    assert capsys.readouterr().out.splitlines()[-1] == "Not ready: 1 check(s) failed."


def test_smoke_without_yes_stops_with_the_cost(capsys):
    assert demo_check.main(["--smoke"]) == 1

    assert capsys.readouterr().out.strip() == (
        "The smoke test calls Claude (about 5 cents). Add --yes to run it."
    )


def test_an_unreachable_database_stops_the_check():
    engine = create_engine("sqlite:////nonexistent-dir/never/test.db")

    checks = demo_check.run_checks(False, TODAY, engine=engine)

    assert len(checks) == 1
    assert checks[0].name == "Database" and checks[0].state == FAIL
    assert checks[0].detail.startswith("Not reachable (")
    assert checks[0].detail.endswith("). Is the db container up?")


def test_migrations_behind_fail_with_their_fix(engine, gh, baseline):
    _versions(engine, orchestrator="0007_forecasts", crm="0003_leads")

    found = _by_name(_run(engine, gh, baseline))

    assert found["Orchestrator migrations"].state == FAIL
    assert found["Orchestrator migrations"].detail == (
        f"at 0007_forecasts, head is {ORCH_HEAD}: run `alembic upgrade head` in the orchestrator."
    )
    assert found["CRM migrations"].state == FAIL
    assert found["CRM migrations"].detail == (
        f"at 0003_leads, head is {CRM_HEAD}: run `alembic upgrade head` in the api container."
    )


def test_the_crm_head_can_be_passed_in(engine, gh, baseline):
    found = _by_name(_run(engine, gh, baseline, crm_head="0005_tasks"))

    assert found["CRM migrations"].state == FAIL
    assert found["CRM migrations"].detail.startswith(f"at {CRM_HEAD}, head is 0005_tasks:")


def test_too_little_crm_data_fails(engine, gh, baseline):
    _crm(engine, accounts=20, leads=49, opportunities=50)

    found = _by_name(_run(engine, gh, baseline))

    assert found["CRM demo data"] == Check(
        "CRM demo data",
        FAIL,
        "20 accounts, 49 leads, 50 opportunities: run the reset (it reseeds the CRM).",
    )


def test_no_sprint_in_progress_and_no_forecast_today_fail(engine, gh, baseline):
    later = date(2026, 11, 30)

    found = _by_name(demo_check.run_checks(False, later, **_options(engine, gh, baseline)))

    history = found["Simulated history"]
    assert history.state == FAIL
    assert history.detail.endswith(" simulated PRs, no sprint in progress today: run the reset.")
    assert found["Forecasts"] == Check(
        "Forecasts",
        FAIL,
        "none saved today: start the collector, or run `python -m sdlc.forecaster now`.",
    )


def test_too_few_simulated_prs_fail(sqlite_engine, gh, baseline):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        synth.build(db, now=NOW)
        db.execute(text("DELETE FROM sdlc_pull_requests WHERE id > 10"))
        db.commit()

    with Session(sqlite_engine) as db:
        check = demo_check.history_check(db, TODAY)

    assert check.state == FAIL
    assert check.detail == "10 simulated PRs, a sprint in progress today: run the reset."


def test_an_api_that_is_down_or_degraded_fails(engine, gh, baseline):
    degraded = {"status": "ok", "service": "orchestrator", "database": "unavailable"}
    http = _http(
        {
            demo_check.ORCH_API: degraded,
            demo_check.CRM_API: httpx.ConnectError("refused"),
        }
    )

    found = _by_name(_run(engine, gh, baseline, http=http))

    assert found["Orchestrator API"].state == FAIL
    assert found["Orchestrator API"].detail.startswith('degraded: {"status":"ok"')
    assert found["CRM API"].detail == f"not answering at {demo_check.CRM_API} (ConnectError)."


class BrokenGitHub(FakeGitHub):
    def get(self, path, **params):
        raise demo_check.GitHubError("GitHub 401 on /repos/acme/widgets: Bad credentials")


def test_a_github_failure_skips_the_github_checks(engine, baseline):
    checks = _run(engine, BrokenGitHub(), baseline, smoke=True)

    names = [check.name for check in checks]
    assert _by_name(checks)["GitHub"] == Check(
        "GitHub",
        FAIL,
        "GitHub 401 on /repos/acme/widgets: Bad credentials. "
        "Check GITHUB_TOKEN and GITHUB_REPO in .env.",
    )
    assert names[names.index("GitHub") + 1 :] == ["ORCHESTRATOR_MODE"]


def test_no_baseline_fails_and_skips_the_release_and_clean_start_checks(engine, gh, tmp_path):
    checks = _run(engine, gh, tmp_path / "none.json")

    names = [check.name for check in checks]
    assert names[names.index("GitHub") + 1 :] == ["Demo baseline", "ORCHESTRATOR_MODE"]
    assert _by_name(checks)["Demo baseline"] == Check(
        "Demo baseline",
        FAIL,
        "none recorded: with the repo clean, run `python -m sdlc.demo_reset baseline`.",
    )


def test_missing_labels_fail(engine, gh, baseline):
    gh.repo_labels = {"demo": "D4C5F9"}

    found = _by_name(_run(engine, gh, baseline))

    assert found["Label demo"].state == PASS
    assert found["Label incident"] == Check(
        "Label incident", FAIL, "missing: run `python -m sdlc.backlog --apply`."
    )


def test_a_missing_deploy_workflow_fails(engine, gh, baseline):
    gh.contents.clear()

    found = _by_name(_run(engine, gh, baseline))

    assert found["Deploy workflow"].state == FAIL
    assert found["Deploy workflow"].detail == (
        "GitHub 404 on /contents/.github/workflows/deploy.yml: Not Found. "
        "Apply ci/proposed/deploy.yml to .github/workflows/."
    )


def test_environments_missing_or_without_a_reviewer_fail(engine, gh, baseline):
    del gh.environments["production"]
    gh.add_environment("production-signoff", reviewer=False)

    found = _by_name(_run(engine, gh, baseline))

    assert found["Environment production"].state == FAIL
    assert found["Environment production"].detail.startswith("GitHub 404 on /environments/")
    assert found["Environment production-signoff"] == Check(
        "Environment production-signoff",
        FAIL,
        "has no required reviewer, so T3 deploys won't wait for your approval: "
        "add yourself under Deployment protection rules.",
    )


def test_a_dirty_repository_fails_the_clean_start(engine, gh, baseline):
    gh.add_issue(20, "Leads page loads slowly", labels=["demo"])
    gh.add_pr(30, "sha30", branch="demo-lead-score")

    found = _by_name(_run(engine, gh, baseline))

    assert found["Clean start"] == Check(
        "Clean start",
        FAIL,
        "3 thing(s) left from a previous demo (Close issue #20 (opened during a demo): "
        "Leads page loads slowly …): run the reset.",
    )
    assert gh.writes() == []


def test_real_work_and_notes_left_behind_do_not_fail_the_clean_start(engine, gh, baseline):
    gh.add_issue(40, "Add a stage filter")
    gh.add_branch("scratch")

    found = _by_name(_run(engine, gh, baseline))

    assert found["Clean start"].state == PASS


def test_smoke_tries_the_newest_merged_real_pr_and_lowest_open_real_issue(engine, gh, baseline):
    gh.add_pr(11, "sha11")
    gh.merge(11, datetime(2026, 10, 10, 12, 0), sha="merge11")
    gh.add_pr(12, "sha12", branch="demo-later")
    gh.merge(12, datetime(2026, 10, 11, 12, 0), sha="merge12")
    calls = []

    def run(args):
        calls.append(args)
        if args[0] == "sdlc.runner":
            return "PR #11 at sha11: ok\nScore 30/100\n"
        return "Issue #1 at version abc: error\nError: " + "x" * 400

    checks = _run(engine, gh, baseline, smoke=True, run=run)

    assert calls == [
        ["sdlc.runner", "try", "11", "--yes"],
        ["sdlc.issue_runner", "try", "1", "--yes"],
    ]
    found = _by_name(checks)
    assert found["Smoke: PR risk"] == Check("Smoke: PR risk", PASS, "#11 scored end to end")
    assert found["Smoke: triage"].state == FAIL
    assert found["Smoke: triage"].detail == "x" * 300
    assert gh.writes() == []


def test_smoke_with_nothing_real_to_try(engine, baseline):
    gh = FakeGitHub()
    empty = baseline.parent / "empty.json"
    demo_reset.save_baseline(demo_reset.snapshot(gh), empty)

    checks = _run(engine, gh, empty, smoke=True, run=lambda args: "Status: ok")

    found = _by_name(checks)
    for name in ("Smoke: PR risk", "Smoke: triage"):
        assert found[name] == Check(name, FAIL, "nothing real to try it on: run the reset first.")
