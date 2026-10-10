import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import issue_runner as issue_runner_module
from sdlc import runner as runner_module
from sdlc.agents import triage
from sdlc.agents.llm import LLMError
from sdlc.agents.triage_comment import MARKER, comment_version, content_version
from sdlc.db import Base
from sdlc.issue_runner import GAVE_UP_MARKER, IssueRunner, main
from sdlc.runner import Runner, run
from sdlc.tables import AgentDecision, Issue
from sdlc.tiers import load_policy
from tests.fakes import FakeGitHub, FakeLLM

NOON = datetime(2026, 10, 6, 12, 0)
TITLE = "Import leads from a CSV file"
BODY = "Sales reps want to upload a spreadsheet of leads."
FOUR = ["module:leads", "type:feature", "priority:p2", "points:3"]


def _answer(**extra):
    return {
        "module": "leads",
        "type": "feature",
        "priority": "p2",
        "estimate_points": 3,
        "duplicate_of": 0,
        "confidence": 0.8,
        "rationale": "Lead import change, sized like past import work.",
        "questions": [],
        **extra,
    }


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        db.add(
            Issue(
                source="synthetic",
                number=11,
                title="Lead import from CSV",
                module="leads",
                type="feature",
                estimate_points=3,
                actual_days=2.5,
                state="closed",
                created_at=datetime(2026, 5, 1, 9, 0),
            )
        )
        db.commit()
    return sqlite_engine


@pytest.fixture
def gh():
    fake = FakeGitHub()
    fake.add_issue(1, TITLE, BODY)
    return fake


@pytest.fixture
def llm():
    return FakeLLM(data=_answer(), model="claude-haiku-4-5-20251001")


@pytest.fixture
def runner(gh, llm, engine):
    return IssueRunner(gh, llm, "enforce", engine=engine)


def _rows(engine):
    with Session(engine) as db:
        query = select(AgentDecision).where(AgentDecision.agent == triage.AGENT)
        return list(db.scalars(query.order_by(AgentDecision.id)))


def _bot_comments(gh, number=1):
    return [c for c in gh.comments if c["number"] == number and c["user"]["type"] == "Bot"]


def test_a_new_issue_gets_four_labels_a_comment_and_one_decision(runner, gh, llm, engine):
    summary = runner.poll_once(NOON)
    assert summary == {"mode": "enforce", "issues": 1, "triaged": 1, "failed": 0, "errors": 0}
    assert sorted(gh.issue_labels[1]) == sorted(FOUR)
    assert gh.repo_labels["module:leads"] == "1B8A94"
    assert gh.repo_labels["points:3"] == "C5CAD1"
    [comment] = _bot_comments(gh)
    assert comment["body"].startswith(MARKER)
    assert "## Triage: leads / feature (p2)" in comment["body"]
    version = content_version(TITLE, BODY)
    assert comment_version(comment["body"]) == version
    [row] = _rows(engine)
    assert (row.trigger, row.head_sha, row.attempt, row.status) == ("opened", version, 1, "ok")
    assert (row.subject_type, row.subject_source, row.subject_id) == ("issue", "github", 1)
    assert row.human_override is None
    assert row.output["module"] == "leads"
    assert {key: value for key, value in row.action_taken.items()} == {
        "module": {"done": "module:leads"},
        "type": {"done": "type:feature"},
        "priority": {"done": "priority:p2"},
        "points": {"done": "points:3"},
        "comment": {"done": "comment"},
    }
    assert f"decision {row.id}" in comment["body"]
    assert len(llm.calls) == 1


def test_a_second_poll_does_nothing(runner, gh, llm, engine):
    runner.poll_once(NOON)
    writes = len(gh.writes())
    runner.poll_once(NOON + timedelta(minutes=10))
    assert len(llm.calls) == 1
    assert len(gh.writes()) == writes
    assert len(_rows(engine)) == 1


def test_editing_the_body_retriages_with_the_trigger_edit(runner, gh, llm, engine):
    runner.poll_once(NOON)
    gh.issues[1]["body"] = BODY + " Up to 5,000 rows."
    llm.data = _answer(priority="p1")
    runner.poll_once(NOON + timedelta(minutes=1))
    first, second = _rows(engine)
    assert second.trigger == "edit"
    assert second.head_sha == content_version(TITLE, gh.issues[1]["body"]) != first.head_sha
    assert "priority:p1" in gh.issue_labels[1] and "priority:p2" not in gh.issue_labels[1]
    [comment] = _bot_comments(gh)  # edited, not added
    assert comment_version(comment["body"]) == second.head_sha


def test_retriage_runs_on_the_same_version_and_is_then_removed(runner, gh, llm, engine):
    runner.poll_once(NOON)
    gh.issue_labels[1].append("retriage")
    runner.poll_once(NOON + timedelta(seconds=30))
    first, second = _rows(engine)
    assert second.trigger == "retriage"
    assert (second.head_sha, second.attempt) == (first.head_sha, 2)
    assert second.action_taken["retriage"] == {"done": "remove retriage"}
    assert "retriage" not in gh.issue_labels[1]
    runner.poll_once(NOON + timedelta(minutes=1))
    assert len(llm.calls) == 2


def test_a_person_changing_module_is_kept_and_recorded(runner, gh, llm, engine):
    runner.poll_once(NOON)
    labels = gh.issue_labels[1]
    labels[labels.index("module:leads")] = "module:pipeline"
    gh.issues[1]["body"] = BODY + " Edited."
    runner.poll_once(NOON + timedelta(minutes=1))
    second = _rows(engine)[-1]
    assert "module:pipeline" in gh.issue_labels[1] and "module:leads" not in gh.issue_labels[1]
    assert second.human_override == {"dimensions": ["module"]}
    assert second.action_taken["module"] == {"skipped": "set by a person"}
    assert "_A human has already set module; left as-is._" in _bot_comments(gh)[0]["body"]
    with Session(engine) as db:  # the person's correction is visible on the row
        issue = db.scalar(select(Issue).where(Issue.source == "github", Issue.number == 1))
        assert issue.module == "pipeline"

    # Later runs leave it alone too, even when the agent's proposal changes.
    llm.data = _answer(module="accounts")
    gh.issues[1]["body"] = BODY + " Edited again."
    runner.poll_once(NOON + timedelta(minutes=2))
    third = _rows(engine)[-1]
    assert third.human_override == {"dimensions": ["module"]}
    assert "module:pipeline" in gh.issue_labels[1] and "module:accounts" not in gh.issue_labels[1]


def test_needs_info_and_possible_duplicate_are_added_once_and_never_removed(
    runner, gh, llm, engine
):
    llm.data = _answer(duplicate_of=11, questions=["Which file formats?"])
    runner.poll_once(NOON)
    labels = gh.issue_labels[1]
    assert labels.count("needs-info") == 1 and labels.count("possible-duplicate") == 1
    assert gh.repo_labels["needs-info"] == "F9A03F"
    assert gh.repo_labels["possible-duplicate"] == "B60205"
    body = _bot_comments(gh)[0]["body"]
    assert "Possibly a duplicate of #11 — linked, not closed." in body
    assert "**Before this is ready to work on:**\n- Which file formats?" in body

    llm.data = _answer()
    gh.issues[1]["body"] = BODY + " Only CSV, about 200 rows."
    runner.poll_once(NOON + timedelta(minutes=1))
    labels = gh.issue_labels[1]
    assert labels.count("needs-info") == 1 and labels.count("possible-duplicate") == 1
    deleted = [path for method, path in gh.requests if method == "DELETE"]
    assert not any("needs-info" in path or "possible-duplicate" in path for path in deleted)
    assert "needs-info" not in _rows(engine)[-1].action_taken


def test_incidents_and_pull_requests_are_skipped(runner, gh, llm, engine):
    gh.add_issue(2, "Checkout is down", "Everything is on fire.", labels=["incident"])
    gh.add_pr(3, "a" * 40)
    summary = runner.poll_once(NOON)
    assert summary["issues"] == 1
    assert len(llm.calls) == 1
    assert {row.subject_id for row in _rows(engine)} == {1}
    assert gh.issue_labels[2] == ["incident"]
    assert gh.issue_labels.get(3, []) == []


def test_three_failures_then_stop_but_retriage_still_runs(runner, gh, llm, engine):
    llm.error = LLMError("timeout", "took too long")
    runner.poll_once(NOON)
    runner.poll_once(NOON + timedelta(minutes=1))  # too soon after the last try
    assert len(llm.calls) == 1
    runner.poll_once(NOON + timedelta(minutes=5))
    runner.poll_once(NOON + timedelta(minutes=10))
    runner.poll_once(NOON + timedelta(minutes=20))
    assert len(llm.calls) == 3
    rows = _rows(engine)
    assert [(row.status, row.attempt) for row in rows] == [("timeout", n) for n in (1, 2, 3)]
    assert [row.trigger for row in rows] == ["opened"] * 3
    assert all(not name.startswith("module:") for name in gh.issue_labels[1])
    [comment] = _bot_comments(gh)
    assert "**Could not triage this issue yet** (timeout)" in comment["body"]

    llm.error = None
    gh.issue_labels[1].append("retriage")
    runner.poll_once(NOON + timedelta(minutes=21))
    last = _rows(engine)[-1]
    assert (last.trigger, last.status, last.attempt) == ("retriage", "ok", 4)
    assert "retriage" not in gh.issue_labels[1]
    assert sorted(gh.issue_labels[1]) == sorted(FOUR)


def _gave_up(gh):
    return [c for c in _bot_comments(gh) if c["body"].startswith(GAVE_UP_MARKER)]


def test_retriage_gives_up_after_three_failures_until_the_label_is_added_again(
    runner, gh, llm, engine
):
    runner.poll_once(NOON)
    llm.error = LLMError("error", "Anthropic API 529")
    gh.label(1, "retriage", NOON + timedelta(minutes=1))
    for minute in (2, 3, 4):  # no backoff: one paid call on every poll
        runner.poll_once(NOON + timedelta(minutes=minute))
    assert len(llm.calls) == 4
    assert [row.trigger for row in _rows(engine)[1:]] == ["retriage"] * 3

    runner.poll_once(NOON + timedelta(minutes=5))
    runner.poll_once(NOON + timedelta(minutes=6))
    assert len(llm.calls) == 4  # stopped
    assert "retriage" in gh.issue_labels[1]
    [comment] = _gave_up(gh)
    last = _rows(engine)[-1]
    assert "gave up" in comment["body"]
    assert f"decision {last.id}" in comment["body"]
    assert "Anthropic API 529" in comment["body"]
    assert "remove it and add it back" in comment["body"]

    # A person removes the label and adds it back: the agent tries again.
    gh.issue_labels[1].remove("retriage")
    llm.error = None
    gh.label(1, "retriage", NOON + timedelta(minutes=7))
    runner.poll_once(NOON + timedelta(minutes=8))
    assert len(llm.calls) == 5
    assert _rows(engine)[-1].status == "ok"
    assert "retriage" not in gh.issue_labels[1]
    assert len(_gave_up(gh)) == 1


def test_an_exception_from_assess_is_a_failed_run(runner, gh, engine, monkeypatch):
    def crash(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(triage, "assess", crash)
    summary = runner.poll_once(NOON)
    assert (summary["failed"], summary["errors"]) == (1, 0)
    [row] = _rows(engine)
    assert (row.status, row.error) == ("error", "RuntimeError: boom")
    assert gh.issue_labels[1] == []


def test_off_makes_no_request(gh, llm, engine):
    assert IssueRunner(gh, llm, "off", engine=engine).poll_once(NOON) == {"mode": "off"}
    assert gh.requests == []
    assert llm.calls == []
    assert _rows(engine) == []


def test_a_person_quoting_the_triage_comment_is_never_a_tier_command(gh, engine):
    gh.add_pr(5, "a" * 40)
    pr_runner = Runner(gh, FakeLLM(), load_policy(), "enforce", engine=engine)
    pr_runner.poll_once(NOON)
    gh.comment(5, f"{MARKER}\n/tier T3 because the triage agent said so")
    pr_runner.poll_once(NOON + timedelta(minutes=1))
    with Session(engine) as db:
        query = select(AgentDecision).where(AgentDecision.agent == "tier_override")
        assert list(db.scalars(query)) == []


def test_the_run_loop_polls_issues_after_pull_requests(gh, engine):
    order = []

    class Recorder:
        def __init__(self, name):
            self.name = name
            self.gh = gh

        def poll_once(self):
            order.append(self.name)
            return {}

    run(Recorder("prs"), 30, sleep=lambda _: None, polls=2, issue_runner=Recorder("issues"))
    assert order == ["prs", "issues", "prs", "issues"]


def test_the_run_loop_forecasts_last_even_when_github_is_rate_limited(gh, engine, monkeypatch):
    order = []

    class Recorder:
        def __init__(self, name):
            self.name = name
            self.gh = gh

        def poll_once(self):
            order.append(self.name)
            return {}

    run(
        Recorder("prs"),
        30,
        sleep=lambda _: None,
        polls=1,
        issue_runner=Recorder("issues"),
        forecast_runner=Recorder("forecasts"),
    )
    assert order == ["prs", "issues", "forecasts"]

    monkeypatch.setattr(gh, "rate_limited", lambda: True, raising=False)
    order.clear()
    run(
        Recorder("prs"),
        30,
        sleep=lambda _: None,
        polls=1,
        issue_runner=Recorder("issues"),
        forecast_runner=Recorder("forecasts"),
    )
    assert order == ["forecasts"]


def test_the_poll_loop_forecaster_does_nothing_when_off(engine):
    from sdlc.forecaster import ForecastRunner

    assert ForecastRunner("off", engine=engine).poll_once() == {"mode": "off"}


@pytest.fixture(autouse=True)
def runner_logs(monkeypatch):
    """Alembic's fileConfig in other tests disables loggers that already exist; undo that."""
    monkeypatch.setattr(runner_module.log, "disabled", False)
    monkeypatch.setattr(issue_runner_module.log, "disabled", False)


def test_cli_once_prints_the_summary(runner, capsys):
    assert main(["once"], runner=runner) == 0
    assert json.loads(capsys.readouterr().out)["triaged"] == 1


def test_cli_dry_run_calls_nothing_and_writes_nothing(runner, gh, llm, engine, capsys):
    assert main(["dry-run", "1"], runner=runner) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["request"]["model"] == "claude-haiku-4-5-20251001"
    assert TITLE in printed["request"]["messages"][0]["content"]
    assert "#11 [closed]" in printed["request"]["messages"][0]["content"]
    assert "effort" not in printed["request"]["output_config"]
    assert printed["max_cost_usd"] > 0
    assert llm.calls == []
    assert gh.writes() == []
    assert _rows(engine) == []


def test_cli_try_without_yes_does_nothing(runner, gh, llm):
    assert main(["try", "1"], runner=runner) == 1
    assert gh.requests == [] and llm.calls == []


def test_cli_try_records_only_a_trial_row_and_never_writes_to_github(
    runner, gh, llm, engine, capsys
):
    assert main(["try", "1", "--yes"], runner=runner) == 0
    out = capsys.readouterr().out
    assert "leads / feature (p2), 3 points" in out
    assert "Nothing was written to GitHub." in out
    assert gh.writes() == []
    [row] = _rows(engine)
    assert (row.trigger, row.attempt, row.status) == ("trial", 4, "ok")

    # A trial never counts: the next poll still triages the issue.
    runner.poll_once(NOON)
    assert [(row.trigger, row.attempt) for row in _rows(engine)] == [("trial", 4), ("opened", 5)]
