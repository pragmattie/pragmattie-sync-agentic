"""Put the demo back to a clean start: undo what a demo opened on GitHub and reset the history.

Only items marked as demo are touched: issues labelled ``demo`` (or ``demo:<x>``), and pull
requests labelled so or opened from a branch starting ``demo-``. Real work is never closed or
deleted. What GitHub looked like before the demos is the baseline, recorded once with
``python -m sdlc.demo_reset baseline`` into ``orchestrator/.demo/baseline.json`` (git-ignored).

``python -m sdlc.demo_reset`` prints what the reset would do and changes nothing; ``--apply``
does it: each GitHub action is sent as planned (one failure doesn't stop the rest), then the
database forgets what it held about the demo items, the simulated history is regenerated for
today, real work is collected from GitHub again and fresh forecasts are saved.
"""

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from sqlalchemy import and_, delete, func, or_, select, update
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from sdlc import issue_runner, synth
from sdlc.agents import signoff, triage_comment
from sdlc.clock import utcnow
from sdlc.db import get_engine
from sdlc.github_client import GitHubClient, GitHubError
from sdlc.tables import (
    AgentDecision,
    Approval,
    Deployment,
    Forecast,
    GateStatus,
    Incident,
    Issue,
    PullRequest,
)

DEMO_LABEL = "demo"
DEMO_BRANCH_PREFIX = "demo-"
INCIDENT_LABEL = "incident"
TIER_LABEL_PREFIX = "tier:"
AGENT_MARKERS = (signoff.COMMENT_MARKER, triage_comment.MARKER, issue_runner.GAVE_UP_MARKER)
SOURCE = "github"
BASELINE_PATH = Path(__file__).resolve().parents[1] / ".demo" / "baseline.json"
NO_BASELINE = (
    "No baseline yet. When the repository is in a clean state, record one with "
    "`python -m sdlc.demo_reset baseline`."
)
DRY_RUN_DATABASE = (
    "Database: regenerate the simulated history for today; forget audit rows, simulated "
    "approvals and gate states about the demo items; collect from GitHub; save forecasts."
)
DRY_RUN_DONE = "Dry run: nothing was changed. Add --apply to do it."


class BaselineError(Exception):
    pass


@dataclass
class Action:
    """One GitHub request the reset would send: ``method`` and ``path`` as the client takes them."""

    what: str
    method: str
    path: str
    body: dict | None = None

    def request(self, repo: str) -> str:
        sent = f"{self.method} {self.path.replace('{repo}', repo)}"
        return f"{sent} {json.dumps(self.body, sort_keys=True)}" if self.body else sent


@dataclass
class Plan:
    actions: list[Action] = field(default_factory=list)
    left_behind: list[str] = field(default_factory=list)
    demo_issues: list[int] = field(default_factory=list)
    demo_prs: list[int] = field(default_factory=list)
    demo_deployments: list[int] = field(default_factory=list)
    real_issues: list[int] = field(default_factory=list)
    real_prs: list[int] = field(default_factory=list)


def _labels(item: dict) -> list[str]:
    return sorted(label["name"] for label in item.get("labels") or [])


def is_demo(item: dict) -> bool:
    """A ``demo`` or ``demo:<x>`` label, or for a pull request a head branch starting ``demo-``."""
    if any(name == DEMO_LABEL or name.startswith(f"{DEMO_LABEL}:") for name in _labels(item)):
        return True
    head = item.get("head") or {}
    return (head.get("ref") or "").startswith(DEMO_BRANCH_PREFIX)


def snapshot(gh: Any) -> dict[str, Any]:
    """What the repository holds now: every issue's state and labels, the PRs, branches, deploys."""
    items = gh.paginate("/repos/{repo}/issues", state="all")
    return {
        "recorded_at": utcnow().replace(microsecond=0).isoformat(),
        "repo": gh.repo,
        "issues": {
            item["number"]: {"state": item["state"], "labels": _labels(item)}
            for item in items
            if "pull_request" not in item
        },
        "prs": sorted(item["number"] for item in items if "pull_request" in item),
        "branches": sorted(branch["name"] for branch in gh.paginate("/repos/{repo}/branches")),
        "deployments": [item["id"] for item in gh.paginate("/repos/{repo}/deployments")],
    }


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, Any] | None:
    if not path.exists():
        return None
    baseline = json.loads(path.read_text())
    baseline["issues"] = {int(number): entry for number, entry in baseline["issues"].items()}
    return baseline


def save_baseline(baseline: dict[str, Any], path: Path = BASELINE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, indent=2) + "\n")


def _remove_label(number: int, name: str, what: str) -> Action:
    return Action(what, "DELETE", f"/repos/{{repo}}/issues/{number}/labels/{quote(name, safe='')}")


def _issue_actions(plan: Plan, baseline: dict[str, Any], items: list[dict]) -> None:
    for item in items:
        number, labels, state = item["number"], _labels(item), item["state"]
        known = baseline["issues"].get(number)
        if known is None and is_demo(item):
            plan.demo_issues.append(number)
            if INCIDENT_LABEL in labels:
                what = f"Take the incident label off issue #{number} (a demo incident)"
                plan.actions.append(_remove_label(number, INCIDENT_LABEL, what))
            if state == "open":
                plan.actions.append(
                    Action(
                        f"Close issue #{number} (opened during a demo): {item['title'][:60]}",
                        "PATCH",
                        f"/repos/{{repo}}/issues/{number}",
                        {"state": "closed", "state_reason": "not_planned"},
                    )
                )
        elif known is None:
            plan.real_issues.append(number)
        else:
            if state != known["state"]:
                verb = "Reopen" if known["state"] == "open" else "Close"
                plan.actions.append(
                    Action(
                        f"{verb} backlog issue #{number}",
                        "PATCH",
                        f"/repos/{{repo}}/issues/{number}",
                        {"state": known["state"]},
                    )
                )
            if labels != known["labels"]:
                change = f"{', '.join(labels) or 'none'} -> {', '.join(known['labels']) or 'none'}"
                plan.actions.append(
                    Action(
                        f"Restore issue #{number}'s labels ({change})",
                        "PATCH",
                        f"/repos/{{repo}}/issues/{number}",
                        {"labels": known["labels"]},
                    )
                )


def _pr_actions(
    gh: Any,
    plan: Plan,
    baseline: dict[str, Any],
    prs: list[dict],
    branches: set[str],
    default: str,
) -> dict[str, int]:
    """Plan the demo PRs' clean-up; returns the merged demo PRs' merge commits, sha to number."""
    kept = set(baseline["branches"]) | {default}
    merged: dict[str, int] = {}
    for pr in prs:
        number = pr["number"]
        if number in baseline["prs"]:
            continue
        if not is_demo(pr):
            plan.real_prs.append(number)
            continue
        plan.demo_prs.append(number)
        if pr["state"] == "open":
            plan.actions.append(
                Action(
                    f"Close demo PR #{number}: {pr['title'][:60]}",
                    "PATCH",
                    f"/repos/{{repo}}/pulls/{number}",
                    {"state": "closed"},
                )
            )
        for comment in gh.paginate(f"/repos/{{repo}}/issues/{number}/comments"):
            if any(marker in (comment.get("body") or "") for marker in AGENT_MARKERS):
                plan.actions.append(
                    Action(
                        f"Delete the agents' comment on PR #{number}",
                        "DELETE",
                        f"/repos/{{repo}}/issues/comments/{comment['id']}",
                    )
                )
        for name in _labels(pr):
            if name.startswith(TIER_LABEL_PREFIX):
                what = f"Remove the {name} label from PR #{number}"
                plan.actions.append(_remove_label(number, name, what))
        head = pr.get("head") or {}
        branch = head.get("ref")
        in_repo = (head.get("repo") or {}).get("full_name") == gh.repo
        if in_repo and branch in branches and branch not in kept:
            plan.actions.append(
                Action(
                    f"Delete branch {branch} (PR #{number})",
                    "DELETE",
                    f"/repos/{{repo}}/git/refs/heads/{quote(branch, safe='/')}",
                )
            )
        if pr.get("merged_at"):
            plan.left_behind.append(f"PR #{number} was merged: its commits stay on {default}.")
            if pr.get("merge_commit_sha"):
                merged[pr["merge_commit_sha"]] = number
    return merged


def plan(gh: Any, baseline: dict[str, Any]) -> Plan:
    """Everything the reset would do on GitHub, in order, and what it leaves behind."""
    if baseline["repo"] != gh.repo:
        raise BaselineError(
            f"The baseline is for {baseline['repo']}, but GITHUB_REPO is {gh.repo}. "
            "Record a baseline for this repository first."
        )
    result = Plan()
    items = gh.paginate("/repos/{repo}/issues", state="all")
    _issue_actions(result, baseline, [item for item in items if "pull_request" not in item])

    default = gh.get("/repos/{repo}").get("default_branch", "main")
    branches = {branch["name"] for branch in gh.paginate("/repos/{repo}/branches")}
    prs = gh.paginate("/repos/{repo}/pulls", state="all")
    merged = _pr_actions(gh, result, baseline, prs, branches, default)

    for deployment in gh.paginate("/repos/{repo}/deployments"):
        number = merged.get(deployment.get("sha"))
        if deployment["id"] in baseline["deployments"] or number is None:
            continue
        result.demo_deployments.append(deployment["id"])
        path = f"/repos/{{repo}}/deployments/{deployment['id']}"
        result.actions.append(
            Action(
                f"Mark deployment {deployment['id']} (PR #{number}) inactive",
                "POST",
                f"{path}/statuses",
                {"state": "inactive"},
            )
        )
        result.actions.append(Action(f"Delete deployment {deployment['id']}", "DELETE", path))
    if result.demo_deployments:
        result.left_behind.append(
            f"The release-gate statuses and the deploy runs' logs of {len(result.demo_deployments)}"
            " demo deployment(s) stay on GitHub."
        )

    heads = {
        (pr.get("head") or {}).get("ref")
        for pr in prs
        if ((pr.get("head") or {}).get("repo") or {}).get("full_name") == gh.repo
    }
    for branch in sorted(branches - set(baseline["branches"]) - heads - {default}):
        result.left_behind.append(f"Branch {branch} has no pull request: kept.")
    if result.real_issues or result.real_prs:
        result.left_behind.append(
            f"Kept as real work (not marked demo): {len(result.real_issues)} issue(s) and "
            f"{len(result.real_prs)} PR(s) new since the baseline."
        )
    if result.demo_issues:
        result.left_behind.append(
            f"{len(result.demo_issues)} issue(s) opened during demos stay on GitHub as closed "
            "(GitHub can't delete issues)."
        )
    return result


def apply_plan(gh: Any, plan: Plan) -> tuple[list[Action], list[tuple[Action, str]]]:
    """Send each action in order; a failure is kept with its error and the rest still run."""
    send: dict[str, Callable[[Action], Any]] = {
        "POST": lambda action: gh.post(action.path, action.body or {}),
        "PATCH": lambda action: gh.patch(action.path, action.body or {}),
        "DELETE": lambda action: gh.delete(action.path),
    }
    done: list[Action] = []
    failed: list[tuple[Action, str]] = []
    for action in plan.actions:
        try:
            send[action.method](action)
        except Exception as error:  # noqa: BLE001 - every failure is reported, none stops the rest
            failed.append((action, str(error)))
        else:
            done.append(action)
    return done, failed


def reset_database(db: Session, demo_issues: list[int], demo_prs: list[int]) -> dict[str, Any]:
    """Forget what the database holds about the demo items, then regenerate the history.

    Deletes the simulated approvals and gate states of demo PRs, the audit rows about demo issues,
    PRs and their releases (source github), and the incidents of demo issues. Real forecasts and
    every planner row are kept. Flushes, never commits.
    """
    pr_ids = select(PullRequest.id).where(
        PullRequest.source == SOURCE, PullRequest.number.in_(demo_prs)
    )
    approvals = db.execute(delete(Approval).where(Approval.pull_request_id.in_(pr_ids))).rowcount
    gates = db.execute(delete(GateStatus).where(GateStatus.pull_request_id.in_(pr_ids))).rowcount
    demo_rows = and_(
        AgentDecision.subject_source == SOURCE,
        or_(
            and_(
                AgentDecision.subject_type == "issue",
                AgentDecision.subject_id.in_(demo_issues),
            ),
            and_(
                AgentDecision.subject_type.in_(("pr", "release")),
                AgentDecision.subject_id.in_(demo_prs),
            ),
        ),
    )
    # a correction and the row it supersedes go together: unlink them first, or MySQL refuses
    db.execute(update(AgentDecision).where(demo_rows).values(supersedes_id=None))
    forgotten = db.execute(delete(AgentDecision).where(demo_rows)).rowcount
    incidents = db.execute(
        delete(Incident).where(
            Incident.source == SOURCE,
            Incident.external_id.in_([f"issue-{number}" for number in demo_issues]),
        )
    ).rowcount
    db.flush()
    synth.reset(db)
    history = synth.build(db)
    return {
        "approvals": approvals,
        "gate_states": gates,
        "forgotten": forgotten,
        "incidents": incidents,
        "history": history,
    }


def collect(engine: Engine, gh: Any) -> dict[str, int]:
    """Collect real work from GitHub, with the policy's release environments."""
    from sdlc.signals.github import Collector, release_environments

    with Session(engine) as db:
        return Collector(db, gh).run(release_environments())


def save_forecasts(engine: Engine) -> dict[str, Any]:
    from sdlc.forecaster import ALWAYS_ON, ForecastRunner

    return ForecastRunner(ALWAYS_ON, engine=engine).poll_once(force=True)


def _counts(counts: dict[str, Any]) -> str:
    return ", ".join(f"{kind}: {count}" for kind, count in counts.items())


def row_counts(db: Session) -> dict[str, int]:
    tables = {
        "issues": Issue,
        "pull requests": PullRequest,
        "deployments": Deployment,
        "incidents": Incident,
        "audit rows": AgentDecision,
        "forecasts": Forecast,
    }
    return {name: db.scalar(select(func.count(table.id))) for name, table in tables.items()}


def run(gh: Any, baseline: dict[str, Any], *, apply: bool, engine: Engine | None = None) -> int:
    """Print the plan, and with ``apply`` carry it out; another repo's baseline is refused."""
    started = time.monotonic()
    result = plan(gh, baseline)
    print(
        f"Baseline recorded {baseline['recorded_at']}: {len(baseline['issues'])} issues, "
        f"{len(baseline['prs'])} PRs, {len(baseline['branches'])} branches."
    )
    print(
        f"Since then: {len(result.demo_issues)} demo issue(s), {len(result.demo_prs)} demo PR(s), "
        f"{len(result.demo_deployments)} deployment(s)."
    )
    if result.actions:
        print("GitHub:")
        for action in result.actions:
            print(f"  {action.what} [{action.request(gh.repo)}]")
    else:
        print("GitHub: nothing to undo.")

    if not apply:
        print(DRY_RUN_DATABASE)
        print(DRY_RUN_DONE)
    else:
        done, failed = apply_plan(gh, result)
        print(f"GitHub: {len(done)} done, {len(failed)} failed.")
        for action, error in failed:
            print(f"  Failed: {action.what}: {error}")
        engine = engine or get_engine()
        with Session(engine) as db:
            reset = reset_database(db, result.demo_issues, result.demo_prs)
            db.commit()
        print(f"Simulated history regenerated for today: {_counts(reset['history'])}.")
        print(f"Audit rows about demo items forgotten: {reset['forgotten']}.")
        try:
            print(f"Collected from GitHub: {_counts(collect(engine, gh))}.")
        except Exception as error:  # noqa: BLE001 - reported; the next collection catches up
            print(f"Collecting from GitHub failed: {error}")
        try:
            print(f"Forecasts saved: {save_forecasts(engine).get('assessed', 0)}.")
        except Exception as error:  # noqa: BLE001 - reported; the next collection saves them
            print(f"Saving forecasts failed: {error}")
        with Session(engine) as db:
            print(f"Rows now: {_counts(row_counts(db))}.")

    print("Left behind:")
    for line in result.left_behind:
        print(f"  {line}")
    print(f"Took {round(time.monotonic() - started)} seconds.")
    return 0


def record_baseline(gh: Any, *, force: bool, path: Path = BASELINE_PATH) -> None:
    if path.exists() and not force:
        print(f"A baseline already exists ({path}). Add --force to replace it.")
        return
    baseline = snapshot(gh)
    save_baseline(baseline, path)
    issues = baseline["issues"].values()
    opened = sum(1 for issue in issues if issue["state"] == "open")
    print(
        f"Baseline recorded for {baseline['repo']}: {len(issues)} issues ({opened} open), "
        f"{len(baseline['prs'])} PRs, {len(baseline['branches'])} branches."
    )


def main(
    argv: list[str] | None = None,
    gh: Any = None,
    engine: Engine | None = None,
    path: Path = BASELINE_PATH,
) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m sdlc.demo_reset", description=__doc__.split("\n")[0]
    )
    parser.add_argument("command", nargs="?", choices=["baseline"], help="record the baseline")
    parser.add_argument("--force", action="store_true", help="replace an existing baseline")
    parser.add_argument("--apply", action="store_true", help="do the reset, not a dry run")
    args = parser.parse_args(argv)

    try:
        gh = gh or GitHubClient()
    except GitHubError as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.command == "baseline":
        record_baseline(gh, force=args.force, path=path)
        return 0
    baseline = load_baseline(path)
    if baseline is None:
        print(NO_BASELINE, file=sys.stderr)
        return 1
    try:
        return run(gh, baseline, apply=args.apply, engine=engine)
    except BaselineError as error:
        print(str(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
