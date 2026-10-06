import json
import logging
from datetime import datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc import runner as runner_module
from sdlc.config import Settings
from sdlc.db import Base
from sdlc.runner import Runner, main, run
from sdlc.signals.github import Collector
from sdlc.tables import AgentDecision, PullRequest
from sdlc.tiers import load_policy
from tests.fakes import FakeGitHub, FakeLLM

SHA = "a" * 40


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    return sqlite_engine


@pytest.fixture
def gh():
    fake = FakeGitHub()
    fake.add_pr(1, SHA)
    return fake


@pytest.fixture
def llm():
    return FakeLLM()


@pytest.fixture
def runner(gh, llm, engine):
    return Runner(gh, llm, load_policy(), "enforce", engine=engine)


@pytest.fixture
def no_build(monkeypatch):
    def refuse(mode):
        raise AssertionError("no client may be built")

    monkeypatch.setattr(runner_module, "_build", refuse)


def _decisions(engine):
    with Session(engine) as db:
        return list(db.scalars(select(AgentDecision).order_by(AgentDecision.id)))


def test_the_mode_defaults_to_off_and_anything_else_is_refused():
    assert Settings(_env_file=None).orchestrator_mode == "off"
    assert Settings(_env_file=None).poll_seconds == 30
    assert Settings(_env_file=None).diff_char_limit == 60000
    assert Settings(_env_file=None, orchestrator_mode="enforce").orchestrator_mode == "enforce"
    with pytest.raises(ValidationError):
        Settings(_env_file=None, orchestrator_mode="on")


def test_try_without_yes_says_so_and_calls_nothing(no_build, capsys):
    assert main(["try", "1"]) == 1
    assert "--yes" in capsys.readouterr().out


def test_try_without_yes_makes_no_request_even_with_a_runner(runner, gh, llm, engine):
    assert main(["try", "1"], runner=runner) == 1
    assert gh.requests == []
    assert llm.calls == []
    assert _decisions(engine) == []


def test_dry_run_shows_the_request_and_a_cost_ceiling_and_writes_nothing(
    runner, gh, llm, engine, capsys
):
    assert main(["dry-run", "1"], runner=runner) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["request"]["model"] == "claude-sonnet-5"
    assert "diff --git a/apps/crm-web/src/App.vue" in printed["request"]["messages"][0]["content"]
    assert printed["max_cost_usd"] > 0
    assert llm.calls == []
    assert gh.writes() == []
    assert _decisions(engine) == []
    with Session(engine) as db:
        assert db.scalar(select(PullRequest)) is None  # collected for the prompt, not kept


def test_try_yes_makes_one_call_records_one_trial_row_and_writes_nothing_to_github(
    runner, gh, llm, engine, capsys
):
    assert main(["try", "1", "--yes"], runner=runner) == 0
    out = capsys.readouterr().out
    assert "Tier: T0" in out
    assert "Nothing was written to GitHub." in out
    assert len(llm.calls) == 1
    assert gh.writes() == []
    [row] = _decisions(engine)
    assert (row.trigger, row.head_sha, row.status) == ("trial", SHA, "ok")

    # A later poll still assesses the commit: a trial never counts as an attempt.
    runner.poll_once(datetime(2026, 10, 6, 12, 0))
    assert len(llm.calls) == 2
    assert [(r.trigger, r.attempt) for r in _decisions(engine)] == [("trial", 4), ("poll", 1)]


def _counts(engine):
    with Session(engine) as db:
        return {
            table.name: db.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
        }


def test_try_collects_a_stored_pr_afresh_and_writes_only_it_and_one_trial_row(
    runner, gh, llm, engine, capsys
):
    with Session(engine) as db:
        Collector(db, gh).collect_pull_request({"number": 1})
        db.commit()
    gh.files[1] = [{"filename": "apps/api/app/routers/pipeline.py"}]  # changed since it was stored
    gh.prs[1]["additions"] = 40
    before = _counts(engine)
    gh.requests.clear()

    assert main(["try", "1", "--yes"], runner=runner) == 0
    assert "Tier: T2" in capsys.readouterr().out  # scored from what GitHub has now
    assert gh.writes() == []
    after = _counts(engine)
    assert {name: after[name] - before[name] for name in after if after[name] != before[name]} == {
        "sdlc_agent_decisions": 1
    }
    [row] = _decisions(engine)
    assert (row.trigger, row.tier) == ("trial", "T2")
    with Session(engine) as db:
        [pr] = db.scalars(select(PullRequest))
        assert (pr.number, pr.additions, pr.module) == (1, 40, "pipeline")


def test_try_twice_records_two_trial_rows(runner, engine):
    main(["try", "1", "--yes"], runner=runner)
    main(["try", "1", "--yes"], runner=runner)
    assert [row.attempt for row in _decisions(engine)] == [4, 5]


def test_once_prints_the_summary(runner, capsys):
    assert main(["once"], runner=runner) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {"mode": "enforce", "prs": 1, "assessed": 1, "failed": 0, "errors": 0}


@pytest.fixture(autouse=True)
def runner_logs(monkeypatch):
    """Alembic's fileConfig in other tests disables loggers that already exist; undo that."""
    monkeypatch.setattr(runner_module.log, "disabled", False)


class Ticks:
    def __init__(self, step):
        self.now = 0.0
        self.step = step
        self.slept = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += self.step


def test_run_keeps_polling_after_a_poll_fails(runner, monkeypatch, caplog):
    calls = []

    def poll_once():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        return {"mode": "enforce"}

    monkeypatch.setattr(runner, "poll_once", poll_once)
    ticks = Ticks(30)
    with caplog.at_level(logging.INFO, "sdlc.runner"):
        run(runner, 30, sleep=ticks.sleep, clock=ticks.clock, polls=3)
    assert len(calls) == 3
    assert ticks.slept == [30, 30, 30]
    assert "The poll failed" in caplog.text


def test_run_skips_polls_while_rate_limited_with_one_log_line(runner, gh, monkeypatch, caplog):
    monkeypatch.setattr(runner, "poll_once", lambda: pytest.fail("polled while limited"))
    gh.limited_until = datetime(2026, 10, 6, 12, 30)
    ticks = Ticks(30)
    with caplog.at_level(logging.INFO, "sdlc.runner"):
        run(runner, 30, sleep=ticks.sleep, clock=ticks.clock, polls=4)
    lines = [r.message for r in caplog.records if "rate limit" in r.message]
    assert lines == ["GitHub's rate limit is spent; skipping polls until 2026-10-06 12:30:00 UTC"]


def test_run_logs_github_calls_once_an_hour(runner, gh, monkeypatch, caplog):
    monkeypatch.setattr(runner, "poll_once", lambda: {})
    gh.sent, gh.not_modified = 120, 90
    ticks = Ticks(1800)
    with caplog.at_level(logging.INFO, "sdlc.runner"):
        run(runner, 1800, sleep=ticks.sleep, clock=ticks.clock, polls=5)
    lines = [r.message for r in caplog.records if "calls sent" in r.message]
    assert lines == ["GitHub calls sent: 120, of which free 304s: 90"] * 2
