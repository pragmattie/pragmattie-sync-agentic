import json
from datetime import date, datetime

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.orm import Session

from sdlc import demo_reset, synth
from sdlc.agents.signoff import COMMENT_MARKER
from sdlc.agents.triage_comment import MARKER as TRIAGE_MARKER
from sdlc.audit import record_decision
from sdlc.db import Base
from sdlc.issue_runner import GAVE_UP_MARKER
from sdlc.tables import (
    AgentDecision,
    Approval,
    Forecast,
    GateStatus,
    Incident,
    PullRequest,
)
from tests.fakes import BOT, REPO, FakeGitHub

DEMO_DAY = datetime(2026, 10, 12, 14, 0)
BACKLOG_LABELS = ["module:leads", "points:3"]


def _repository() -> FakeGitHub:
    """The repository as it was before any demo: backlog issues, a real PR, a deployment."""
    gh = FakeGitHub()
    gh.add_issue(1, "Score leads by activity", labels=BACKLOG_LABELS)
    gh.add_issue(2, "Merge duplicate accounts", labels=["module:accounts"])
    gh.add_issue(3, "Retire the old export", labels=["module:platform"])
    gh.close_issue(3, datetime(2026, 9, 30, 9, 0))
    gh.add_pr(10, "sha10", branch="feat-forecast-chart")
    gh.add_deployment("sha-old")
    return gh


def _demo(gh: FakeGitHub) -> dict:
    """What a demo leaves behind, next to real work that happened meanwhile."""
    gh.add_issue(20, "Leads page loads slowly", labels=["demo", "module:leads"])
    gh.add_issue(21, "Pipeline totals are wrong", labels=["demo", "incident", "sev2"])
    gh.close_issue(2, DEMO_DAY)
    gh.issue_labels[1] = ["module:pipeline", "points:5"]

    gh.add_pr(30, "sha30", branch="demo-lead-score")
    gh.issue_labels[30] = ["tier:T2"]
    agent_comment = gh.comment(30, f"{COMMENT_MARKER}\nRisk 55, T2.", user=BOT)
    triage = gh.comment(30, f"{TRIAGE_MARKER}\nTriage.", user=BOT)
    person_comment = gh.comment(30, "Looks good to me.")

    gh.add_pr(31, "sha31", branch="quick-fix")
    gh.issue_labels[31] = ["demo", "tier:T0"]
    gh.merge(31, DEMO_DAY, sha="merge31")
    demo_deploy = gh.add_deployment("merge31")
    gh.deployment_status(demo_deploy["id"], "success", DEMO_DAY)

    gh.add_branch("scratch")

    gh.add_issue(40, "Add a stage filter to the pipeline", labels=["module:pipeline"])
    gh.add_pr(41, "sha41", branch="agent/40-stage-filter")
    gh.issue_labels[41] = ["tier:T1"]
    real_comment = gh.comment(41, f"{COMMENT_MARKER}\nRisk 30, T1.", user=BOT)
    gh.add_pr(42, "sha42", branch="agent/39-forecast")
    gh.merge(42, DEMO_DAY, sha="merge42")
    real_deploy = gh.add_deployment("merge42")
    gh.deployment_status(real_deploy["id"], "success", DEMO_DAY)
    gh.add_pr(43, "sha43", branch="demo-from-a-fork", head_repo="someone/widgets")
    gh.add_branch("agent/44-real")
    return {
        "agent_comment": agent_comment,
        "triage_comment": triage,
        "person_comment": person_comment,
        "real_comment": real_comment,
        "demo_deploy": demo_deploy,
        "real_deploy": real_deploy,
    }


@pytest.fixture
def scenario():
    gh = _repository()
    baseline = demo_reset.snapshot(gh)
    made = _demo(gh)
    gh.requests.clear()
    return gh, baseline, made


def test_snapshot_records_issues_prs_branches_and_deployments():
    gh = _repository()

    baseline = demo_reset.snapshot(gh)

    assert baseline["repo"] == REPO
    assert baseline["issues"] == {
        1: {"state": "open", "labels": BACKLOG_LABELS},
        2: {"state": "open", "labels": ["module:accounts"]},
        3: {"state": "closed", "labels": ["module:platform"]},
    }
    assert baseline["prs"] == [10]
    assert baseline["branches"] == ["feat-forecast-chart", "main"]
    assert baseline["deployments"] == [gh.deployments[0]["id"]]
    assert gh.writes() == []


def test_is_demo_by_label_or_branch():
    assert demo_reset.is_demo({"labels": [{"name": "demo"}]})
    assert demo_reset.is_demo({"labels": [{"name": "demo:sales"}]})
    assert demo_reset.is_demo({"labels": [], "head": {"ref": "demo-lead-score"}})
    assert not demo_reset.is_demo({"labels": [{"name": "demonstration"}]})
    assert not demo_reset.is_demo({"labels": [], "head": {"ref": "agent/79-demo-reset"}})
    assert not demo_reset.is_demo({"labels": [{"name": "module:leads"}]})


def test_reset_undoes_the_demo_and_only_the_demo(scenario):
    gh, baseline, made = scenario

    plan = demo_reset.plan(gh, baseline)
    done, failed = demo_reset.apply_plan(gh, plan)

    assert failed == []
    assert len(done) == len(plan.actions)
    assert plan.demo_issues == [20, 21]
    assert plan.demo_prs == [30, 31, 43]
    assert plan.demo_deployments == [made["demo_deploy"]["id"]]
    assert plan.real_issues == [40]
    assert plan.real_prs == [41, 42]

    # The demo issues are closed as not planned, the incident label taken off.
    for number in (20, 21):
        assert gh.issues[number]["state"] == "closed"
        assert gh.issues[number]["state_reason"] == "not_planned"
    assert gh.issue_labels[21] == ["demo", "sev2"]
    # The backlog is as it was.
    assert gh.issues[2]["state"] == "open"
    assert gh.issue_labels[1] == BACKLOG_LABELS
    assert gh.issues[3]["state"] == "closed"
    # The open demo PR is closed, its tier label and agents' comments gone, a person's kept.
    assert gh.prs[30]["state"] == "closed"
    assert gh.issue_labels[30] == []
    assert gh.issue_labels[31] == ["demo"]
    kept = {comment["id"] for comment in gh.comments}
    assert made["person_comment"]["id"] in kept
    assert made["real_comment"]["id"] in kept
    assert made["agent_comment"]["id"] not in kept
    assert made["triage_comment"]["id"] not in kept
    # Demo branches are deleted; scratch, the baseline's and real work's branches stay.
    assert sorted(gh.branches) == [
        "agent/39-forecast",
        "agent/40-stage-filter",
        "agent/44-real",
        "feat-forecast-chart",
        "main",
        "scratch",
    ]
    # Only the demo deployment is deleted, after being marked inactive.
    assert [d["id"] for d in gh.deployments] == [
        baseline["deployments"][0],
        made["real_deploy"]["id"],
    ]

    # Nothing is written to an untouched baseline item or to real work.
    untouched = [f"/issues/{n}/" for n in (3, 40, 41, 42)] + [f"/pulls/{n}/" for n in (10, 41, 42)]
    untouched += [
        f"/deployments/{n}/" for n in (*baseline["deployments"], made["real_deploy"]["id"])
    ]
    for _, path in gh.writes():
        assert not f"{path}/".startswith(tuple(untouched)), path
    real_comment = f"/issues/comments/{made['real_comment']['id']}"
    assert ("DELETE", real_comment) not in gh.writes()

    assert demo_reset.plan(gh, baseline).actions == []


def test_plan_lists_each_action_and_what_stays(scenario):
    gh, baseline, made = scenario

    plan = demo_reset.plan(gh, baseline)

    deploy = made["demo_deploy"]["id"]
    assert [action.what for action in plan.actions] == [
        "Restore issue #1's labels (module:pipeline, points:5 -> module:leads, points:3)",
        "Reopen backlog issue #2",
        "Close issue #20 (opened during a demo): Leads page loads slowly",
        "Take the incident label off issue #21 (a demo incident)",
        "Close issue #21 (opened during a demo): Pipeline totals are wrong",
        "Close demo PR #30: Change 30",
        "Delete the agents' comment on PR #30",
        "Delete the agents' comment on PR #30",
        "Remove the tier:T2 label from PR #30",
        "Delete branch demo-lead-score (PR #30)",
        "Remove the tier:T0 label from PR #31",
        "Delete branch quick-fix (PR #31)",
        "Close demo PR #43: Change 43",
        f"Mark deployment {deploy} (PR #31) inactive",
        f"Delete deployment {deploy}",
    ]
    assert plan.actions[2] == demo_reset.Action(
        plan.actions[2].what,
        "PATCH",
        "/repos/{repo}/issues/20",
        {"state": "closed", "state_reason": "not_planned"},
    )
    assert plan.left_behind == [
        "PR #31 was merged: its commits stay on main.",
        "The release-gate statuses and the deploy runs' logs of 1 demo deployment(s) stay on "
        "GitHub.",
        "Branch agent/44-real has no pull request: kept.",
        "Branch scratch has no pull request: kept.",
        "Kept as real work (not marked demo): 1 issue(s) and 2 PR(s) new since the baseline.",
        "2 issue(s) opened during demos stay on GitHub as closed (GitHub can't delete issues).",
    ]


def test_the_triage_agents_gave_up_comment_on_a_demo_pr_is_deleted(scenario):
    gh, baseline, made = scenario
    gave_up = gh.comment(30, f"{GAVE_UP_MARKER}\nThe triage agent gave up.", user=BOT)

    demo_reset.apply_plan(gh, demo_reset.plan(gh, baseline))

    kept = {comment["id"] for comment in gh.comments}
    assert gave_up["id"] not in kept
    assert made["person_comment"]["id"] in kept


def test_no_kept_as_real_work_line_when_nothing_is_new():
    gh = _repository()
    baseline = demo_reset.snapshot(gh)
    gh.add_issue(20, "Leads page loads slowly", labels=["demo", "module:leads"])

    plan = demo_reset.plan(gh, baseline)

    assert (plan.real_issues, plan.real_prs) == ([], [])
    assert not [line for line in plan.left_behind if line.startswith("Kept as real work")]


def test_a_failed_action_does_not_stop_the_rest(scenario):
    gh, baseline, _ = scenario
    gh.fail_writes = {"PATCH"}

    plan = demo_reset.plan(gh, baseline)
    done, failed = demo_reset.apply_plan(gh, plan)

    assert {action.method for action, _ in failed} == {"PATCH"}
    assert all("fake failure" in error for _, error in failed)
    assert {action.method for action in done} == {"DELETE", "POST"}
    assert len(done) + len(failed) == len(plan.actions)


def test_a_baseline_for_another_repository_is_refused(scenario, tmp_path, capsys):
    gh, baseline, _ = scenario
    path = tmp_path / "baseline.json"
    demo_reset.save_baseline({**baseline, "repo": "acme/other"}, path)

    with pytest.raises(demo_reset.BaselineError, match="Record a baseline for this repository"):
        demo_reset.plan(gh, {**baseline, "repo": "acme/other"})
    assert demo_reset.main([], gh=gh, path=path) == 1

    assert capsys.readouterr().err.strip() == (
        f"The baseline is for acme/other, but GITHUB_REPO is {REPO}. "
        "Record a baseline for this repository first."
    )
    assert gh.writes() == []


def test_with_no_baseline_it_says_how_to_record_one(tmp_path, capsys):
    gh = _repository()

    assert demo_reset.main([], gh=gh, path=tmp_path / "baseline.json") == 1

    assert "python -m sdlc.demo_reset baseline" in capsys.readouterr().err
    assert gh.writes() == []


def test_baseline_command_records_and_keeps_an_existing_one(tmp_path, capsys):
    gh = _repository()
    path = tmp_path / ".demo" / "baseline.json"

    assert demo_reset.main(["baseline"], gh=gh, path=path) == 0
    assert capsys.readouterr().out.strip() == (
        f"Baseline recorded for {REPO}: 3 issues (2 open), 1 PRs, 2 branches."
    )
    recorded = path.read_text()
    assert json.loads(recorded)["issues"]["1"]["labels"] == BACKLOG_LABELS
    assert demo_reset.load_baseline(path)["issues"][1]["labels"] == BACKLOG_LABELS

    gh.add_issue(5)
    assert demo_reset.main(["baseline"], gh=gh, path=path) == 0
    assert capsys.readouterr().out.strip() == (
        f"A baseline already exists ({path}). Add --force to replace it."
    )
    assert path.read_text() == recorded

    assert demo_reset.main(["baseline", "--force"], gh=gh, path=path) == 0
    assert "4 issues (3 open)" in capsys.readouterr().out
    assert gh.writes() == []


def test_the_dry_run_changes_nothing_and_prints_what_it_would_do(scenario, tmp_path, capsys):
    gh, baseline, _ = scenario
    path = tmp_path / "baseline.json"
    demo_reset.save_baseline(baseline, path)

    assert demo_reset.main([], gh=gh, path=path) == 0

    assert gh.writes() == []
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == f"Baseline recorded {baseline['recorded_at']}: 3 issues, 1 PRs, 2 branches."
    assert lines[1] == "Since then: 2 demo issue(s), 3 demo PR(s), 1 deployment(s)."
    assert lines[2] == "GitHub:"
    assert lines[3] == (
        "  Restore issue #1's labels (module:pipeline, points:5 -> module:leads, points:3) "
        f'[PATCH /repos/{REPO}/issues/1 {{"labels": ["module:leads", "points:3"]}}]'
    )
    branch = f"  Delete branch demo-lead-score (PR #30) [DELETE /repos/{REPO}/git/refs/heads/"
    assert f"{branch}demo-lead-score]" in lines
    assert demo_reset.DRY_RUN_DATABASE in lines
    assert demo_reset.DRY_RUN_DONE in lines
    assert lines.index("Left behind:") > lines.index(demo_reset.DRY_RUN_DONE)
    assert lines[-1].startswith("Took ") and lines[-1].endswith(" seconds.")


def test_nothing_to_undo(tmp_path, capsys):
    gh = _repository()
    path = tmp_path / "baseline.json"
    demo_reset.save_baseline(demo_reset.snapshot(gh), path)

    assert demo_reset.main([], gh=gh, path=path) == 0

    assert "GitHub: nothing to undo." in capsys.readouterr().out.splitlines()


@pytest.fixture
def session(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _pr(db: Session, number: int, source: str = "github") -> PullRequest:
    pr = PullRequest(
        source=source,
        external_id=f"pr-{number}",
        number=number,
        title=f"Change {number}",
        created_at=DEMO_DAY,
    )
    db.add(pr)
    db.flush()
    db.add(Approval(pull_request_id=pr.id, approver_id="sim", tier="T3", requested_at=DEMO_DAY))
    db.add(
        GateStatus(
            pull_request_id=pr.id,
            tier="T3",
            state="pending",
            would_be="pending",
            missing=[],
            description="Waiting",
            mode="shadow",
            updated_at=DEMO_DAY,
        )
    )
    return pr


def _decision(db: Session, agent: str, subject_type: str, subject_id: int, source="github"):
    return record_decision(
        db,
        agent=agent,
        agent_version="v1",
        subject_type=subject_type,
        subject_source=source,
        subject_id=subject_id,
        trigger="poll",
        now=DEMO_DAY,
    )


def _forecast(db: Session, source: str) -> None:
    db.add(
        Forecast(
            created_at=DEMO_DAY,
            as_of=date(2026, 10, 12),
            kind="epic",
            subject="M6 Release",
            source=source,
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


def _incident(db: Session, number: int) -> None:
    db.add(
        Incident(
            source="github",
            external_id=f"issue-{number}",
            title="Down",
            severity="sev2",
            opened_at=DEMO_DAY,
        )
    )


def test_reset_database_forgets_only_the_demo_items(session):
    demo_pr, real_pr = _pr(session, 30), _pr(session, 41)
    _pr(session, 31, source="synthetic")
    _decision(session, "pr_risk", "pr", 30)
    _decision(session, "release_gate", "release", 30)
    _decision(session, "triage", "issue", 20)
    kept = [
        _decision(session, "pr_risk", "pr", 41),
        _decision(session, "triage", "issue", 40),
        _decision(session, "triage", "issue", 30),  # an issue that shares a demo PR's number
        _decision(session, "planner", "epic", 30),
        _decision(session, "forecaster", "epic", 20, source="github"),
    ]
    _incident(session, 20)
    _incident(session, 40)
    _forecast(session, "github")
    _forecast(session, "mixed")
    session.commit()

    result = demo_reset.reset_database(session, [20, 21], [30, 31])
    session.commit()

    assert result["forgotten"] == 3
    assert result["approvals"] == 1
    assert result["gate_states"] == 1
    assert result["incidents"] == 1
    real = select(AgentDecision.id).where(AgentDecision.subject_source == "github")
    assert set(session.scalars(real)) == {row.id for row in kept}
    approvals = set(session.scalars(select(Approval.pull_request_id)))
    gates = set(session.scalars(select(GateStatus.pull_request_id)))
    assert demo_pr.id not in approvals and real_pr.id in approvals
    assert demo_pr.id not in gates and real_pr.id in gates
    assert set(
        session.scalars(select(Incident.external_id).where(Incident.source == "github"))
    ) == {"issue-40"}
    assert set(session.scalars(select(Forecast.source))) == {"github", "mixed"}
    assert synth.has_synthetic(session)
    assert result["history"]["sprints"] == synth.SPRINT_COUNT
    synthetic = select(func.count(PullRequest.id)).where(PullRequest.source == "synthetic")
    assert session.scalar(synthetic) == result["history"]["pull_requests"]


def test_a_superseded_pair_of_demo_rows_is_deleted_without_error(session, sqlite_engine):
    original = _decision(session, "pr_risk", "pr", 30)
    correction = _decision(session, "pr_risk", "pr", 30)
    correction.supersedes_id = original.id
    session.commit()
    statements = []
    event.listen(
        sqlite_engine,
        "before_cursor_execute",
        lambda conn, cursor, statement, *rest: statements.append(statement.split()[0]),
    )

    result = demo_reset.reset_database(session, [], [30])
    session.commit()

    assert result["forgotten"] == 2
    real = select(func.count(AgentDecision.id)).where(AgentDecision.subject_source == "github")
    assert session.scalar(real) == 0
    # MySQL checks the self-reference row by row, so the pair is unlinked before the delete
    assert statements.index("UPDATE") < statements.index("DELETE", statements.index("UPDATE"))


def test_apply_resets_github_and_the_database(scenario, sqlite_engine, monkeypatch, capsys):
    gh, baseline, _ = scenario
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        _pr(db, 30)
        _decision(db, "pr_risk", "pr", 30)
        db.commit()
    monkeypatch.setattr(demo_reset, "collect", lambda engine, client: {"issues": 6})
    monkeypatch.setattr(demo_reset, "save_forecasts", lambda engine: {"assessed": 4})

    assert demo_reset.run(gh, baseline, apply=True, engine=sqlite_engine) == 0

    out = capsys.readouterr().out.splitlines()
    assert "GitHub: 15 done, 0 failed." in out
    assert "Audit rows about demo items forgotten: 1." in out
    assert "Collected from GitHub: issues: 6." in out
    assert "Forecasts saved: 4." in out
    assert any(
        line.startswith("Simulated history regenerated for today: engineers:") for line in out
    )
    assert any(line.startswith("Rows now: issues:") for line in out)
    assert demo_reset.plan(gh, baseline).actions == []


def test_apply_with_a_read_only_token_lists_every_action_as_failed(
    scenario, sqlite_engine, monkeypatch, capsys
):
    gh, baseline, _ = scenario
    gh.fail_writes = {"POST", "PATCH", "DELETE"}
    Base.metadata.create_all(sqlite_engine)
    monkeypatch.setattr(demo_reset, "collect", lambda engine, client: {"issues": 6})
    monkeypatch.setattr(demo_reset, "save_forecasts", lambda engine: {"assessed": 4})

    assert demo_reset.run(gh, baseline, apply=True, engine=sqlite_engine) == 0

    out = capsys.readouterr().out.splitlines()
    assert "GitHub: 0 done, 15 failed." in out
    assert sum(line.startswith("  Failed: ") for line in out) == 15


def test_demo_issue_numbers_are_the_demo_issues_not_pull_requests():
    items = [
        {"number": 20, "labels": [{"name": "demo"}]},
        {"number": 21, "labels": [{"name": "demo:sales"}]},
        {"number": 22, "labels": [{"name": "module:leads"}]},
        {"number": 30, "labels": [{"name": "demo"}], "pull_request": {}},
    ]

    assert demo_reset.demo_issue_numbers(items) == {20, 21}


def _agent_pr(ref: str, *, labels=(), head_repo: str = REPO) -> dict:
    return {
        "labels": [{"name": name} for name in labels],
        "head": {"ref": ref, "repo": {"full_name": head_repo}},
        "base": {"ref": "main", "repo": {"full_name": REPO}},
    }


def test_is_demo_pr_by_the_builders_branch_for_a_demo_issue():
    demo_issues = {240}

    assert demo_reset.is_demo_pr(_agent_pr("agent/240-8-4-leads-clear-filters"), demo_issues)
    assert not demo_reset.is_demo_pr(_agent_pr("agent/239-8-3-milestone"), demo_issues)
    assert not demo_reset.is_demo_pr(_agent_pr("agent/240"), demo_issues)
    assert not demo_reset.is_demo_pr(_agent_pr("feature/agent/240-x"), demo_issues)
    # a fork's branch of the same shape is judged by is_demo alone
    fork = _agent_pr("agent/240-8-4-leads", head_repo="someone/widgets")
    assert not demo_reset.is_demo_pr(fork, demo_issues)
    assert demo_reset.is_demo_pr({**fork, "labels": [{"name": "demo"}]}, demo_issues)
    # a label or a demo- branch still makes it demo work, whatever its issue
    assert demo_reset.is_demo_pr(_agent_pr("agent/1-x", labels=["demo"]), set())
    assert demo_reset.is_demo_pr(_agent_pr("demo-lead-score"), set())
    assert not demo_reset.is_demo_pr(_agent_pr("quick-fix"), demo_issues)


def _agent_pr_for_issue_240(gh: FakeGitHub, *, demo: bool, merged: bool = True) -> dict:
    """The live demo's shape: the builder's PR for issue #240, with no labels of its own."""
    gh.add_issue(240, "Leads: a Clear filters button", labels=["demo"] if demo else [])
    gh.add_pr(241, "sha241", branch="agent/240-8-4-leads-clear-filters")
    gh.issue_labels[241] = ["tier:T1"]
    comment = gh.comment(241, f"{COMMENT_MARKER}\nRisk 30, T1.", user=BOT)
    deploy = None
    if merged:
        gh.merge(241, DEMO_DAY, sha="merge241")
        deploy = gh.add_deployment("merge241")
        gh.deployment_status(deploy["id"], "success", DEMO_DAY)
    gh.requests.clear()
    return {"comment": comment, "deploy": deploy}


def test_a_merged_agent_pr_for_a_demo_issue_is_planned_like_a_demo_pr():
    gh = _repository()
    baseline = demo_reset.snapshot(gh)
    made = _agent_pr_for_issue_240(gh, demo=True)

    plan = demo_reset.plan(gh, baseline)

    deploy = made["deploy"]["id"]
    assert plan.demo_issues == [240]
    assert plan.demo_prs == [241]
    assert plan.real_prs == []
    assert plan.demo_deployments == [deploy]
    assert [action.what for action in plan.actions] == [
        "Close issue #240 (opened during a demo): Leads: a Clear filters button",
        "Delete the agents' comment on PR #241",
        "Remove the tier:T1 label from PR #241",
        "Delete branch agent/240-8-4-leads-clear-filters (PR #241)",
        f"Mark deployment {deploy} (PR #241) inactive",
        f"Delete deployment {deploy}",
    ]
    assert "PR #241 was merged: its commits stay on main." in plan.left_behind
    # no extra API calls: the demo issues come from the issues the plan already fetches
    assert gh.requests == [
        ("GET", "/issues"),
        ("GET", ""),
        ("GET", "/branches"),
        ("GET", "/pulls"),
        ("GET", "/issues/241/comments"),
        ("GET", "/deployments"),
    ]

    demo_reset.apply_plan(gh, plan)
    assert made["comment"]["id"] not in {comment["id"] for comment in gh.comments}
    assert gh.issue_labels[241] == []
    assert "agent/240-8-4-leads-clear-filters" not in gh.branches
    assert deploy not in [d["id"] for d in gh.deployments]
    assert demo_reset.plan(gh, baseline).actions == []


def test_the_same_pr_for_a_real_issue_is_kept_as_real_work():
    gh = _repository()
    baseline = demo_reset.snapshot(gh)
    _agent_pr_for_issue_240(gh, demo=False)

    plan = demo_reset.plan(gh, baseline)

    assert (plan.demo_prs, plan.real_prs) == ([], [241])
    assert (plan.real_issues, plan.demo_deployments) == ([240], [])
    assert plan.actions == []


def test_an_open_agent_pr_for_a_demo_issue_is_closed():
    gh = _repository()
    baseline = demo_reset.snapshot(gh)
    _agent_pr_for_issue_240(gh, demo=True, merged=False)

    plan = demo_reset.plan(gh, baseline)

    assert plan.demo_prs == [241]
    assert (
        demo_reset.Action(
            "Close demo PR #241: Change 241",
            "PATCH",
            "/repos/{repo}/pulls/241",
            {"state": "closed"},
        )
        in plan.actions
    )
    demo_reset.apply_plan(gh, plan)
    assert gh.prs[241]["state"] == "closed"


def test_a_forks_agent_branch_for_a_demo_issue_is_real_work():
    gh = _repository()
    baseline = demo_reset.snapshot(gh)
    gh.add_issue(240, "Leads: a Clear filters button", labels=["demo"])
    gh.add_pr(241, "sha241", branch="agent/240-from-a-fork", head_repo="someone/widgets")

    plan = demo_reset.plan(gh, baseline)

    assert (plan.demo_prs, plan.real_prs) == ([], [241])


def test_apply_forgets_the_rows_about_an_agent_pr_for_a_demo_issue(
    sqlite_engine, monkeypatch, capsys
):
    gh = _repository()
    baseline = demo_reset.snapshot(gh)
    _agent_pr_for_issue_240(gh, demo=True)
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        _pr(db, 241)
        _decision(db, "pr_risk", "pr", 241)
        _decision(db, "release_gate", "release", 241)
        db.commit()
    monkeypatch.setattr(demo_reset, "collect", lambda engine, client: {"issues": 6})
    monkeypatch.setattr(demo_reset, "save_forecasts", lambda engine: {"assessed": 4})

    assert demo_reset.run(gh, baseline, apply=True, engine=sqlite_engine) == 0

    assert "Audit rows about demo items forgotten: 2." in capsys.readouterr().out.splitlines()
    with Session(sqlite_engine) as db:
        github = select(func.count(AgentDecision.id)).where(
            AgentDecision.subject_source == "github"
        )
        assert db.scalar(github) == 0
        assert db.scalar(select(func.count(Approval.id))) == 0
        assert db.scalar(select(func.count(GateStatus.id))) == 0


def _board_after_three_demos() -> tuple[FakeGitHub, dict]:
    """Demo issues #240 and #247 (closed, merged PRs) and #253 (not planned) on the board, next to
    a real issue's card, a baseline demo issue's card and a pull request's card."""
    gh = _repository()
    gh.add_issue(5, "An old demo issue", labels=["demo"])
    baseline = demo_reset.snapshot(gh)
    for number in (240, 247):
        gh.add_issue(number, f"Demo change {number}", labels=["demo"])
        gh.add_pr(number + 1, f"sha{number + 1}", branch=f"agent/{number}-demo-change")
        gh.merge(number + 1, DEMO_DAY, sha=f"merge{number + 1}")
        gh.close_issue(number, DEMO_DAY)
    gh.add_issue(253, "Demo change 253", labels=["demo"])
    gh.close_issue(253, DEMO_DAY)
    gh.issues[253]["state_reason"] = "not_planned"
    gh.add_issue(239, "Show the milestone on the board", labels=["module:platform"])
    gh.branches = [name for name in gh.branches if not name.startswith("agent/")]
    for number in (239, 240, 247, 253, 5):
        gh.add_card(number)
    gh.add_card(241, pull_request=True)
    gh.comment(241, f"{COMMENT_MARKER}\nRisk 30, T1.", user=BOT)
    gh.requests.clear()
    return gh, baseline


ARCHIVES = [
    "Archive the board card of #240",
    "Archive the board card of #247",
    "Archive the board card of #253",
]


def _archives(plan: demo_reset.Plan) -> list[str]:
    return [action.what for action in plan.actions if action.method == demo_reset.GRAPHQL]


def test_the_demo_issues_cards_are_archived_after_every_other_action():
    gh, baseline = _board_after_three_demos()

    plan = demo_reset.plan(gh, baseline)

    assert plan.demo_issues == [240, 247, 253]
    assert [action.what for action in plan.actions] == [
        "Delete the agents' comment on PR #241",
        *ARCHIVES,
    ]
    assert plan.actions[-1] == demo_reset.Action(
        "Archive the board card of #253",
        "GRAPHQL",
        "archiveProjectV2Item",
        {"project": "PVT_1", "item": "PVTI_253"},
    )
    assert plan.actions[-1].request(REPO) == (
        'GRAPHQL archiveProjectV2Item {"item": "PVTI_253", "project": "PVT_1"}'
    )
    assert not [line for line in plan.left_behind if line.startswith("Board cards")]

    done, failed = demo_reset.apply_plan(gh, plan)

    assert failed == []
    assert len(done) == len(plan.actions)
    # the real issue's, the baseline demo issue's and the pull request's cards stay
    assert [item["id"] for item in gh.board] == ["PVTI_239", "PVTI_5", "PVTI_241"]
    assert gh.issues[240]["state"] == "closed"


def test_a_second_plan_after_applying_archives_nothing():
    gh, baseline = _board_after_three_demos()
    demo_reset.apply_plan(gh, demo_reset.plan(gh, baseline))

    again = demo_reset.plan(gh, baseline)

    assert _archives(again) == []
    assert again.actions == []


def test_a_refused_archive_fails_only_the_archives():
    gh, baseline = _board_after_three_demos()
    gh.add_issue(260, "An open demo issue", labels=["demo"])
    gh.add_card(260)
    gh.board_write_error = "GitHub GraphQL: Resource not accessible by integration"

    plan = demo_reset.plan(gh, baseline)
    done, failed = demo_reset.apply_plan(gh, plan)

    assert [action.what for action, _ in failed] == [*ARCHIVES, "Archive the board card of #260"]
    assert all("Resource not accessible" in error for _, error in failed)
    assert [action.what for action in done] == [
        "Close issue #260 (opened during a demo): An open demo issue",
        "Delete the agents' comment on PR #241",
    ]
    assert gh.issues[260]["state"] == "closed"
    assert len(gh.board) == 7


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        ("GitHub 403 on /graphql: Forbidden", "GitHub 403 on /graphql: Forbidden"),
        (None, "No project 1 in the pragmattie organization"),
    ],
)
def test_a_board_that_cannot_be_read_is_left_behind_in_one_line(error, reason):
    gh, baseline = _board_after_three_demos()
    gh.add_issue(260, "An open demo issue", labels=["demo"])
    if error:
        gh.board_error = error
    else:
        gh.board = None

    plan = demo_reset.plan(gh, baseline)
    done, failed = demo_reset.apply_plan(gh, plan)

    assert _archives(plan) == []
    board_lines = [line for line in plan.left_behind if line.startswith("Board cards")]
    assert board_lines == [f"Board cards of demo issues were not archived: {reason}."]
    assert plan.left_behind[-1] == board_lines[0]
    assert failed == []
    assert [action.what for action in done] == [
        "Close issue #260 (opened during a demo): An open demo issue",
        "Delete the agents' comment on PR #241",
    ]


def test_the_dry_run_lists_the_archives_and_sends_nothing(tmp_path, capsys):
    gh, baseline = _board_after_three_demos()
    path = tmp_path / "baseline.json"
    demo_reset.save_baseline(baseline, path)

    assert demo_reset.main([], gh=gh, path=path) == 0

    assert gh.writes() == []
    assert not [query for query, _ in gh.graphql_calls if "mutation" in query]
    assert len(gh.board) == 6
    lines = capsys.readouterr().out.splitlines()
    assert (
        "  Archive the board card of #247 "
        '[GRAPHQL archiveProjectV2Item {"item": "PVTI_247", "project": "PVT_1"}]'
    ) in lines
