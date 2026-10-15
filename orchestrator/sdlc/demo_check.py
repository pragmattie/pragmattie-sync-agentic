"""The pre-demo check: whether everything is ready, item by item, with the fix for what isn't.

``python -m sdlc.demo_check`` runs inside the orchestrator container and checks the database,
both Alembic histories, the CRM demo data, the simulated history, today's forecasts, both APIs,
GitHub access, the release setup, a clean start against the demo baseline and the agents' mode.
``--smoke --yes`` also makes one real, paid call per agent (about 5 cents) with the runners'
``try`` commands, which record a trial row each and write nothing to GitHub.

The container can't see the CRM's migrations, so ``scripts/demo.ps1 check`` passes the CRM's
head with ``--crm-head`` (from the api container); run elsewhere, the check reads
``apps/api/migrations`` itself. Exit code 1 means at least one check failed; a WARN doesn't.
"""

import argparse
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import func, select, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import demo_reset
from sdlc.clock import utcnow
from sdlc.config import get_settings
from sdlc.db import get_engine
from sdlc.github_client import GitHubClient, GitHubError
from sdlc.migrations_filter import VERSION_TABLE
from sdlc.release_runner import DEPLOY_WORKFLOW
from sdlc.tables import Forecast, PullRequest, Sprint

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
CRM_API = "http://api:8000/api/v1/health"
ORCH_API = "http://localhost:8001/api/v1/health"
MIN_CRM = {"accounts": 20, "leads": 50, "opportunities": 50}
MIN_SIMULATED_PRS = 300
ORCH_MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
CRM_MIGRATIONS = Path(__file__).resolve().parents[2] / "apps" / "api" / "migrations"
CRM_VERSION_TABLE = "alembic_version"
SYNTHETIC = "synthetic"
TIMEOUT_SECONDS = 5.0
SMOKE_COST = "The smoke test calls Claude (about 5 cents). Add --yes to run it."
NOTHING_TO_TRY = "nothing real to try it on: run the reset first."
LABELS = {
    demo_reset.DEMO_LABEL: "exists (marks what the reset cleans up)",
    demo_reset.INCIDENT_LABEL: "exists (raises a real incident)",
}


@dataclass
class Check:
    name: str
    state: str
    detail: str


def line(check: Check) -> str:
    return f"  {check.state:<4}  {check.name:<24} {check.detail}"


def summary(checks: list[Check]) -> str:
    failed = sum(1 for check in checks if check.state == FAIL)
    return f"Not ready: {failed} check(s) failed." if failed else "Ready for the demo."


def script_head(location: Path) -> str | None:
    return ScriptDirectory(str(location)).get_current_head()


def current_revision(engine: Engine, version_table: str) -> str | None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"version_table": version_table})
        return context.get_current_revision()


def migration_check(
    name: str, engine: Engine, version_table: str, head: str | None, where: str
) -> Check:
    if head is None:
        return Check(name, WARN, "can't see its migrations from here: pass --crm-head.")
    current = current_revision(engine, version_table)
    if current == head:
        return Check(name, PASS, f"at {head}")
    return Check(
        name,
        FAIL,
        f"at {current or 'nothing'}, head is {head}: run `alembic upgrade head` in {where}.",
    )


def crm_data_check(engine: Engine) -> Check:
    with engine.connect() as connection:
        counts = {
            table: connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar_one()
            for table in MIN_CRM
        }
    found = ", ".join(f"{count} {table}" for table, count in counts.items())
    if any(counts[table] < least for table, least in MIN_CRM.items()):
        return Check("CRM demo data", FAIL, f"{found}: run the reset (it reseeds the CRM).")
    return Check("CRM demo data", PASS, found)


def history_check(db: Session, today: date) -> Check:
    prs = db.scalar(select(func.count(PullRequest.id)).where(PullRequest.source == SYNTHETIC))
    sprint = db.scalars(
        select(Sprint)
        .where(Sprint.source == SYNTHETIC, Sprint.start_date <= today, Sprint.end_date >= today)
        .order_by(Sprint.start_date.desc())
    ).first()
    if sprint is None or prs < MIN_SIMULATED_PRS:
        running = "a" if sprint else "no"
        detail = f"{prs} simulated PRs, {running} sprint in progress today: run the reset."
        return Check("Simulated history", FAIL, detail)
    end = sprint.end_date
    detail = f"{prs} simulated PRs; {sprint.name} runs to {end:%a %b} {end.day}"
    return Check("Simulated history", PASS, detail)


def forecasts_check(db: Session, today: date) -> Check:
    saved = db.scalar(select(func.count(Forecast.id)).where(Forecast.as_of == today))
    if saved:
        return Check("Forecasts", PASS, f"{saved} saved today")
    detail = "none saved today: start the collector, or run `python -m sdlc.forecaster now`."
    return Check("Forecasts", FAIL, detail)


def health_check(name: str, url: str, http: httpx.Client) -> Check:
    try:
        response = http.get(url, timeout=TIMEOUT_SECONDS)
    except httpx.HTTPError as error:
        return Check(name, FAIL, f"not answering at {url} ({type(error).__name__}).")
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict) and body.get("status") == "ok" and body.get("database") == "ok":
        return Check(name, PASS, "healthy")
    return Check(name, FAIL, f"degraded: {response.text}")


def release_checks(gh: Any, default_branch: str, environments: tuple[str, str]) -> list[Check]:
    checks = []
    labels = {label["name"] for label in gh.paginate("/repos/{repo}/labels")}
    for name, exists in LABELS.items():
        if name in labels:
            checks.append(Check(f"Label {name}", PASS, exists))
        else:
            detail = "missing: run `python -m sdlc.backlog --labels-only --apply`."
            checks.append(Check(f"Label {name}", FAIL, detail))

    path = f".github/workflows/{DEPLOY_WORKFLOW}"
    try:
        gh.get(f"/repos/{{repo}}/contents/{path}", ref=default_branch)
        checks.append(Check("Deploy workflow", PASS, f"{DEPLOY_WORKFLOW} is on the default branch"))
    except GitHubError as error:
        detail = f"{error}. Apply ci/proposed/{DEPLOY_WORKFLOW} to .github/workflows/."
        checks.append(Check("Deploy workflow", FAIL, detail))

    production, signoff = environments
    for name in (production, signoff):
        check = f"Environment {name}"
        try:
            environment = gh.get(f"/repos/{{repo}}/environments/{quote(name, safe='')}")
        except GitHubError as error:
            detail = f"{error}. Create it under Settings > Environments."
            checks.append(Check(check, FAIL, detail))
            continue
        if name != signoff:
            checks.append(Check(check, PASS, "exists"))
            continue
        rules = environment.get("protection_rules") or []
        if any(rule.get("type") == "required_reviewers" for rule in rules):
            checks.append(Check(check, PASS, "exists with a required reviewer"))
        else:
            detail = (
                "has no required reviewer, so T3 deploys won't wait for your approval: "
                "add yourself under Deployment protection rules."
            )
            checks.append(Check(check, FAIL, detail))

    try:
        gh.paginate("/repos/{repo}/deployments")
        detail = "can read deployments (the reset also needs write, which can't be tested safely)"
        checks.append(Check("Token: deployments", PASS, detail))
    except GitHubError as error:
        checks.append(Check("Token: deployments", FAIL, f"{error}. The token needs Deployments."))
    return checks


def clean_start_check(gh: Any, baseline: dict[str, Any]) -> Check:
    try:
        actions = demo_reset.plan(gh, baseline).actions
    except demo_reset.BaselineError as error:
        return Check("Clean start", FAIL, str(error))
    if not actions:
        return Check("Clean start", PASS, "the repository matches the demo baseline")
    more = " …" if len(actions) > 1 else ""
    detail = (
        f"{len(actions)} thing(s) left from a previous demo ({actions[0].what}{more}): "
        "run the reset."
    )
    return Check("Clean start", FAIL, detail)


def mode_check(mode: str) -> Check:
    if mode == "enforce":
        detail = (
            "enforce: v2 governs this repository; the release gate holds deploys and "
            "risk-gate shows its real state."
        )
        return Check("ORCHESTRATOR_MODE", PASS, detail)
    if mode == "shadow":
        detail = (
            "shadow: v2 says what it would do but gates nothing, and since cutover nothing else "
            "governs this repository. Set ORCHESTRATOR_MODE=enforce in .env (docs/cutover.md)."
        )
        return Check("ORCHESTRATOR_MODE", FAIL, detail)
    if mode == "observe":
        detail = (
            "observe: v2 posts nothing, and since cutover nothing else governs this repository "
            "(v1 is archived). Set ORCHESTRATOR_MODE=enforce in .env, or follow the rollback in "
            "docs/cutover.md step 8."
        )
        return Check("ORCHESTRATOR_MODE", FAIL, detail)
    detail = "off: the agents won't react during the demo. Set it to shadow in .env."
    return Check("ORCHESTRATOR_MODE", FAIL, detail)


def newest_merged_real_pr(gh: Any) -> int | None:
    merged = [
        pr
        for pr in gh.paginate("/repos/{repo}/pulls", state="closed")
        if pr.get("merged_at") and not demo_reset.is_demo(pr)
    ]
    return max(merged, key=lambda pr: pr["merged_at"])["number"] if merged else None


def lowest_open_real_issue(gh: Any) -> int | None:
    numbers = [
        item["number"]
        for item in gh.paginate("/repos/{repo}/issues", state="open")
        if "pull_request" not in item and not demo_reset.is_demo(item)
    ]
    return min(numbers) if numbers else None


def run_module(args: list[str]) -> str:
    done = subprocess.run(
        [sys.executable, "-m", *args], capture_output=True, text=True, check=False
    )
    return done.stdout + done.stderr


def _is_ok(output: str) -> bool:
    """The trial's status: "Status: ok", or the runners' first line "... #n at ...: ok"."""
    first = output.strip().splitlines()[0] if output.strip() else ""
    return "Status: ok" in output or first.endswith(": ok")


def smoke_checks(gh: Any, run: Callable[[list[str]], str]) -> list[Check]:
    trials = [
        ("Smoke: PR risk", "sdlc.runner", newest_merged_real_pr(gh)),
        ("Smoke: triage", "sdlc.issue_runner", lowest_open_real_issue(gh)),
    ]
    checks = []
    for name, module, number in trials:
        if number is None:
            checks.append(Check(name, FAIL, NOTHING_TO_TRY))
            continue
        output = run([module, "try", str(number), "--yes"])
        if _is_ok(output):
            checks.append(Check(name, PASS, f"#{number} scored end to end"))
        else:
            checks.append(Check(name, FAIL, output.strip()[-300:]))
    return checks


def run_checks(
    smoke: bool,
    today: date,
    *,
    engine: Engine | None = None,
    gh: Any = None,
    http: httpx.Client | None = None,
    mode: str | None = None,
    crm_head: str | None = None,
    baseline_path: Path = demo_reset.BASELINE_PATH,
    environments: tuple[str, str] | None = None,
    run: Callable[[list[str]], str] = run_module,
) -> list[Check]:
    """Every check in order; the database's failure stops the rest, GitHub's skips its own."""
    engine = engine or get_engine()
    checks: list[Check] = []
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception as error:  # noqa: BLE001 - any failure to connect is reported, then stop
        reason = (str(error).strip().splitlines() or [type(error).__name__])[0][:200]
        detail = f"Not reachable ({reason}). Is the db container up?"
        return [Check("Database", FAIL, detail)]
    checks.append(Check("Database", PASS, "reachable"))

    checks.append(
        migration_check(
            "Orchestrator migrations",
            engine,
            VERSION_TABLE,
            script_head(ORCH_MIGRATIONS),
            "the orchestrator",
        )
    )
    if crm_head is None and CRM_MIGRATIONS.exists():
        crm_head = script_head(CRM_MIGRATIONS)
    checks.append(
        migration_check("CRM migrations", engine, CRM_VERSION_TABLE, crm_head, "the api container")
    )
    try:
        checks.append(crm_data_check(engine))
    except Exception as error:  # noqa: BLE001 - e.g. the CRM's tables aren't there yet
        detail = f"can't count it ({type(error).__name__}): run the reset (it reseeds the CRM)."
        checks.append(Check("CRM demo data", FAIL, detail))
    with Session(engine) as db:
        checks.append(history_check(db, today))
        checks.append(forecasts_check(db, today))

    client = http or httpx.Client()
    try:
        checks.append(health_check("Orchestrator API", ORCH_API, client))
        checks.append(health_check("CRM API", CRM_API, client))
    finally:
        if http is None:
            client.close()

    github_ok = False
    try:
        gh = gh or GitHubClient()
        repository = gh.get("/repos/{repo}")
    except GitHubError as error:
        detail = f"{error}. Check GITHUB_TOKEN and GITHUB_REPO in .env."
        checks.append(Check("GitHub", FAIL, detail))
    else:
        github_ok = True
        checks.append(Check("GitHub", PASS, f"token works for {repository['full_name']}"))
        baseline = demo_reset.load_baseline(baseline_path)
        if baseline is None:
            detail = "none recorded: with the repo clean, run `python -m sdlc.demo_reset baseline`."
            checks.append(Check("Demo baseline", FAIL, detail))
        else:
            checks.append(Check("Demo baseline", PASS, f"recorded {baseline['recorded_at']}"))
            if environments is None:
                from sdlc.signals.github import release_environments

                environments = release_environments()
            default = repository.get("default_branch", "main")
            checks.extend(release_checks(gh, default, environments))
            checks.append(clean_start_check(gh, baseline))

    checks.append(mode_check(mode or get_settings().orchestrator_mode))
    if smoke and github_ok:
        checks.extend(smoke_checks(gh, run))
    return checks


def main(argv: list[str] | None = None, **kwargs: Any) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.demo_check", description=__doc__.split("\n")[0]
    )
    parser.add_argument("--smoke", action="store_true", help="one real, paid call per agent")
    parser.add_argument("--yes", action="store_true", help="confirm the smoke test's cost")
    parser.add_argument("--crm-head", help="the CRM's Alembic head, from the api container")
    args = parser.parse_args(argv)

    if args.smoke and not args.yes:
        print(SMOKE_COST)
        return 1
    checks = run_checks(args.smoke, utcnow().date(), crm_head=args.crm_head, **kwargs)
    for check in checks:
        print(line(check))
    print(summary(checks))
    return 1 if any(check.state == FAIL for check in checks) else 0


if __name__ == "__main__":
    sys.exit(main())
