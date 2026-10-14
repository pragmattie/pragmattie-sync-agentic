import json
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc import forecaster, plan_runner, synth
from sdlc.agents import planner
from sdlc.agents.llm import LLMError
from sdlc.audit import TRIAL
from sdlc.config import get_settings
from sdlc.db import Base
from sdlc.plan_runner import (
    MAX_ATTEMPTS,
    MAX_PER_DAY,
    PlannerRunner,
    find_milestone,
    latest_proposal,
    main,
)
from sdlc.tables import AgentDecision, Epic, Forecast, Issue
from tests.fakes import FakeGitHub, FakeLLM

MILESTONE = "M5 Delivery forecasting"
LATER = "M6 Planning"
NOW = datetime(2026, 10, 7, 10, 0)  # a Wednesday
TARGET = date(2026, 10, 9)


def _answer():
    return {
        "summary": "Defer the low-priority work.",
        "options": [
            {"action": "defer", "title": "Defer #102", "items": [102], "rationale": "p3."},
            {"action": "split", "title": "Split #101", "items": [101], "rationale": "Large."},
        ],
        "recommended": 1,
        "confidence": 0.7,
    }


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        for number, name in ((6, MILESTONE), (7, LATER)):
            db.add(
                Epic(
                    source="github",
                    external_id=f"milestone-{number}",
                    number=number,
                    name=name,
                    state="open",
                    due_on=TARGET if name == MILESTONE else None,
                    created_at=datetime(2026, 9, 1, 9),
                )
            )
        for number in (101, 102):
            db.add(
                Issue(
                    number=number,
                    external_id=f"issue-{number}",
                    title=f"Issue {number}",
                    source="github",
                    epic=MILESTONE,
                    created_at=datetime(2026, 9, 1, 9),
                )
            )
        db.commit()
    return sqlite_engine


def save_forecast(engine, subject=MILESTONE, *, on_time=0.4, key="a", at=NOW, source="github"):
    """Save a forecast of ``subject`` as the forecaster would; ``key`` makes its inputs hash."""
    with Session(engine) as db:
        row = Forecast(
            created_at=at,
            as_of=at.date(),
            kind="epic",
            subject=subject,
            source=source,
            trigger="schedule",
            inputs_hash=key * 64,
            remaining_items=2,
            remaining_points=5,
            end_date=TARGET,
            p50=date(2026, 10, 12),
            p85=date(2026, 10, 14),
            on_time_probability=on_time,
            throughput_mean=1.0,
            history_days=10,
            runs=200,
            seed=7,
        )
        db.add(row)
        db.commit()
        return row.id


def test_the_planner_takes_the_saved_rows_items_ahead(engine):
    with Session(engine) as db:
        row = db.get(Forecast, save_forecast(engine))
        assert plan_runner.as_epic_forecast(row).ahead_items == 0
        row.ahead_items = 10
        assert plan_runner.as_epic_forecast(row).ahead_items == 10


def test_the_planner_takes_the_saved_rows_gate_days(engine):
    with Session(engine) as db:
        row = db.get(Forecast, save_forecast(engine))
        assert plan_runner.as_epic_forecast(row).gate_days == 0
        row.gate_days = 2
        assert plan_runner.as_epic_forecast(row).gate_days == 2


def planner_rows(engine):
    with Session(engine) as db:
        query = select(AgentDecision).where(AgentDecision.agent == planner.AGENT)
        return list(db.scalars(query.order_by(AgentDecision.id)))


@pytest.fixture
def llm():
    return FakeLLM(data=_answer())


def test_a_slipping_milestone_gets_one_proposal_and_a_second_poll_none(engine, llm):
    forecast_id = save_forecast(engine)
    runner = PlannerRunner(llm, "on", engine=engine)

    first = runner.poll_once(NOW)
    second = runner.poll_once(NOW + timedelta(hours=1))

    assert first == {"mode": "on", "slipping": 1, "proposed": 1, "failed": 0, "skipped": 0}
    assert second["proposed"] == 0 and second["skipped"] == 1
    assert len(llm.calls) == 1
    [row] = planner_rows(engine)
    assert (row.subject_type, row.subject_source, row.subject_id) == ("epic", "github", forecast_id)
    assert (row.trigger, row.head_sha, row.attempt, row.status) == ("schedule", "a" * 40, 1, "ok")
    assert row.model_id == "claude-sonnet-5"
    assert (row.prompt_version, row.prompt_hash) == (planner.PROMPT_VERSION, planner.PROMPT_HASH)
    assert (row.input_tokens, row.output_tokens, row.latency_ms) == (1200, 150, 900)
    assert row.output["summary"] == "Defer the low-priority work."
    assert [option["action"] for option in row.output["options"]] == ["defer", "split"]
    assert row.output["options"][0]["effect"]["from"]["p50"] == "2026-10-12"
    assert row.output["options"][1]["effect"] is None
    assert row.output["recommended"] == 1
    assert row.output["confidence"] == 0.7
    assert row.output["dropped"] == []
    assert row.output["milestone"] == "Delivery forecasting (M5)"
    assert row.output["forecast_id"] == forecast_id
    assert row.output["cost_usd"] == 0.0039


def test_a_new_forecast_of_the_same_milestone_gets_a_new_proposal(engine, llm):
    save_forecast(engine, key="a")
    runner = PlannerRunner(llm, "on", engine=engine)
    runner.poll_once(NOW)
    newer = save_forecast(engine, key="b", at=NOW + timedelta(minutes=30))

    assert runner.poll_once(NOW + timedelta(minutes=31))["proposed"] == 1

    assert len(llm.calls) == 2
    assert [row.subject_id for row in planner_rows(engine)][-1] == newer
    assert planner_rows(engine)[-1].head_sha == "b" * 40


def test_three_failures_five_minutes_apart_then_no_more(engine):
    save_forecast(engine)
    llm = FakeLLM(error=LLMError("timeout", "Timed out."))
    runner = PlannerRunner(llm, "on", engine=engine)

    assert runner.poll_once(NOW)["failed"] == 1
    assert runner.poll_once(NOW + timedelta(minutes=4))["skipped"] == 1  # too soon
    assert runner.poll_once(NOW + timedelta(minutes=5))["failed"] == 1
    assert runner.poll_once(NOW + timedelta(minutes=10))["failed"] == 1
    assert runner.poll_once(NOW + timedelta(minutes=15))["skipped"] == 1
    assert runner.poll_once(NOW + timedelta(hours=3))["skipped"] == 1

    assert len(llm.calls) == MAX_ATTEMPTS
    rows = planner_rows(engine)
    assert [(row.attempt, row.status, row.error) for row in rows] == [
        (1, "timeout", "Timed out."),
        (2, "timeout", "Timed out."),
        (3, "timeout", "Timed out."),
    ]
    assert all(row.output["cost_usd"] is None for row in rows)


def test_the_fourth_call_of_the_day_is_the_last_and_the_fifth_is_refused(engine):
    llm = FakeLLM(error=LLMError("error", "Broken."))
    runner = PlannerRunner(llm, "on", engine=engine)
    for index, key in enumerate("abcde"):
        at = NOW + timedelta(hours=index)
        save_forecast(engine, key=key, at=at)  # the items churn: a new forecast every hour
        summary = runner.poll_once(at)
        assert summary["failed" if index < MAX_PER_DAY else "skipped"] == 1

    assert len(llm.calls) == MAX_PER_DAY  # failed calls count too
    # The next day (UTC) the cap starts again.
    assert runner.poll_once(datetime(2026, 10, 8, 0, 1))["failed"] == 1
    assert len(llm.calls) == MAX_PER_DAY + 1


def test_the_daily_cap_is_per_milestone(engine, llm):
    runner = PlannerRunner(llm, "on", engine=engine)
    for index, key in enumerate("abcd"):
        at = NOW + timedelta(hours=index)
        save_forecast(engine, key=key, at=at)
        runner.poll_once(at)
    save_forecast(engine, LATER, key="z", at=NOW + timedelta(hours=5))

    assert runner.poll_once(NOW + timedelta(hours=5))["proposed"] == 1
    assert len(llm.calls) == MAX_PER_DAY + 1


def test_a_milestone_that_isnt_slipping_is_never_called(engine, llm):
    save_forecast(engine, on_time=0.9)
    save_forecast(engine, LATER, on_time=None, key="b")  # no target: not slipping
    save_forecast(engine, "Simulated epic", key="c", source="synthetic")  # never planned

    summary = PlannerRunner(llm, "on", engine=engine).poll_once(NOW)

    assert summary["slipping"] == 0
    assert llm.calls == []
    assert planner_rows(engine) == []


def test_only_the_latest_forecast_is_judged(engine, llm):
    save_forecast(engine, on_time=0.4, key="a")
    save_forecast(engine, on_time=0.95, key="b", at=NOW + timedelta(minutes=1))

    PlannerRunner(llm, "on", engine=engine).poll_once(NOW + timedelta(minutes=2))

    assert llm.calls == []


def test_planner_mode_off_makes_no_call(engine, llm):
    save_forecast(engine)

    assert PlannerRunner(llm, "off", engine=engine).poll_once(NOW) == {"mode": "off"}
    assert llm.calls == []
    assert planner_rows(engine) == []


def test_it_never_makes_a_github_request(engine, llm, monkeypatch):
    from sdlc import github_client

    gh = FakeGitHub()
    monkeypatch.setattr(github_client, "GitHubClient", lambda *args, **kwargs: gh)
    save_forecast(engine)
    runner = PlannerRunner(llm, "on", engine=engine)

    runner.poll_once(NOW)
    runner.poll_once(NOW + timedelta(minutes=10))

    assert not hasattr(runner, "gh")
    assert gh.requests == [] and gh.sent == 0
    with Session(engine) as db:
        # It writes nothing but its audit rows: no issue or milestone changed.
        assert db.scalars(select(Issue.epic).order_by(Issue.number)).all() == [MILESTONE] * 2
        assert db.scalar(select(func.count()).select_from(Epic)) == 2
        assert db.scalar(select(func.count()).select_from(Forecast)) == 1


def test_latest_proposal_has_the_fixed_shape(engine, llm):
    forecast_id = save_forecast(engine)
    PlannerRunner(llm, "on", engine=engine).poll_once(NOW)

    with Session(engine) as db:
        proposal = latest_proposal(db, MILESTONE)
        assert latest_proposal(db, LATER) is None

    assert set(proposal) == {
        "summary",
        "options",
        "recommended",
        "confidence",
        "created_at",
        "forecast_id",
        "current",
    }
    assert proposal["summary"] == "Defer the low-priority work."
    assert proposal["recommended"] == 1
    assert proposal["confidence"] == 0.7
    assert proposal["created_at"] == "2026-10-07T10:00:00"
    assert proposal["forecast_id"] == forecast_id
    assert proposal["current"] is True
    defer, split = proposal["options"]
    assert set(defer) == {"action", "title", "items", "rationale", "effect"}
    assert defer["items"] == [102]
    assert set(defer["effect"]) == {"p50", "p85", "on_time_probability", "from"}
    assert set(defer["effect"]["from"]) == {"p50", "p85", "on_time_probability"}
    assert split["effect"] is None


def test_latest_proposal_leaves_out_failed_and_trial_rows(engine, llm, capsys):
    save_forecast(engine)
    PlannerRunner(FakeLLM(error=LLMError("timeout", "Slow.")), "on", engine=engine).poll_once(NOW)
    assert main(["try", "--yes"], engine=engine, llm=llm) == 0

    with Session(engine) as db:
        assert latest_proposal(db, MILESTONE) is None


def test_an_imported_v1_planner_row_is_never_the_proposal_or_counted_toward_the_cap(engine):
    forecast_id = save_forecast(engine)
    with Session(engine) as db:
        db.add(
            AgentDecision(
                created_at=NOW,
                agent=planner.AGENT,
                agent_version="v1",
                subject_type="epic",
                subject_source="v1",
                subject_id=forecast_id,
                head_sha="a" * 40,
                trigger="schedule",
                status="ok",
                output={"summary": "v1's proposal.", "options": [], "recommended": 0},
            )
        )
        db.commit()

        assert latest_proposal(db, MILESTONE) is None
        assert plan_runner.calls_today(db, MILESTONE, NOW) == 0


def test_the_dashboard_shows_the_proposal_and_marks_it_stale_after_the_forecast_moves(engine, llm):
    first = save_forecast(engine, key="a")
    runner = PlannerRunner(llm, "on", engine=engine)
    runner.poll_once(NOW)

    with Session(engine) as db:
        proposals = forecaster.dashboard(db)["proposals"]
    assert list(proposals) == [MILESTONE]
    assert proposals[MILESTONE]["forecast_id"] == first
    assert proposals[MILESTONE]["current"] is True

    # The forecast moves (to one no longer slipping): the old proposal stays, out of date.
    save_forecast(engine, key="b", on_time=0.9, at=NOW + timedelta(hours=1))
    runner.poll_once(NOW + timedelta(hours=1))
    with Session(engine) as db:
        proposals = forecaster.dashboard(db)["proposals"]
    assert proposals[MILESTONE]["forecast_id"] == first
    assert proposals[MILESTONE]["current"] is False
    assert len(llm.calls) == 1


def test_the_dashboard_has_no_proposals_without_planner_rows(engine):
    save_forecast(engine)
    with Session(engine) as db:
        assert forecaster.dashboard(db)["proposals"] == {}


@pytest.mark.parametrize("value", ["5", "M5", "m5", MILESTONE])
def test_find_milestone_takes_the_m_number_or_the_title(engine, value):
    with Session(engine) as db:
        assert find_milestone(db, value).name == MILESTONE
        assert find_milestone(db, "6").name == LATER  # never GitHub's milestone number (7)
        assert find_milestone(db, "9") is None


def test_dry_run_shows_the_request_and_a_cost_ceiling_and_calls_nothing(engine, llm, capsys):
    forecast_id = save_forecast(engine)
    llm.max_tokens = 2000

    assert main(["dry-run"], engine=engine, llm=llm) == 0

    result = json.loads(capsys.readouterr().out)
    assert llm.calls == []
    assert planner_rows(engine) == []
    assert result["milestone"] == MILESTONE
    assert result["forecast_id"] == forecast_id
    assert result["slipping"] is True
    request = result["request"]
    assert request["model"] == "claude-sonnet-5"
    assert request["max_tokens"] == 2000
    assert request["system"][0]["text"] == planner.SYSTEM_PROMPT
    assert request["output_config"] == {
        "format": {"type": "json_schema", "schema": planner.SCHEMA},
        "effort": "medium",
    }
    user = request["messages"][0]["content"]
    assert "#101" in user and "#102" in user
    assert "Next milestone: Planning (M6)" in user
    assert result["max_cost_usd"] > 0


def test_dry_run_takes_a_named_milestone_even_when_not_slipping(engine, llm, capsys):
    save_forecast(engine)
    later = save_forecast(engine, LATER, on_time=None, key="b")

    assert main(["dry-run", "--milestone", "M6"], engine=engine, llm=llm) == 0

    result = json.loads(capsys.readouterr().out)
    assert (result["milestone"], result["forecast_id"], result["slipping"]) == (LATER, later, False)
    assert llm.calls == []


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["dry-run", "--milestone", "9"], "No open milestone matches '9'."),
        (["try", "--yes", "--milestone", "Nope"], "No open milestone matches 'Nope'."),
        (["dry-run", "--milestone", "6"], "M6 Planning has no saved forecast yet."),
        (["dry-run"], "No open milestone is slipping."),
    ],
)
def test_an_unknown_or_unforecast_milestone_says_so_and_stops(engine, llm, capsys, argv, message):
    save_forecast(engine, on_time=0.9)

    assert main(argv, engine=engine, llm=llm) == 1

    assert capsys.readouterr().out.strip() == message
    assert llm.calls == []
    assert planner_rows(engine) == []


def test_try_without_yes_calls_nothing(engine, llm, capsys):
    save_forecast(engine)

    assert main(["try"], engine=engine, llm=llm) == 1

    assert "--yes" in capsys.readouterr().out
    assert llm.calls == []
    assert planner_rows(engine) == []


def test_try_yes_writes_one_trial_row_and_leaves_the_poll_alone(engine, llm, capsys):
    forecast_id = save_forecast(engine)

    assert main(["try", "--yes", "--milestone", "5"], engine=engine, llm=llm) == 0

    out = capsys.readouterr().out
    assert "Defer the low-priority work." in out
    assert "Nothing was written to GitHub." in out
    assert len(llm.calls) == 1
    [row] = planner_rows(engine)
    assert (row.trigger, row.status, row.subject_id) == (TRIAL, "ok", forecast_id)
    assert row.attempt == MAX_ATTEMPTS + 1  # clear of the poll's attempts
    # A trial is neither a proposal nor one of the day's calls: the poll still drafts once.
    assert PlannerRunner(llm, "on", engine=engine).poll_once(NOW)["proposed"] == 1
    assert len(llm.calls) == 2


def test_trial_calls_dont_count_toward_the_daily_cap(engine, llm, capsys):
    save_forecast(engine)
    for _ in range(MAX_PER_DAY + 1):
        assert main(["try", "--yes"], engine=engine, llm=llm) == 0

    with Session(engine) as db:
        assert plan_runner.calls_today(db, MILESTONE, NOW) == 0
    assert PlannerRunner(llm, "on", engine=engine).poll_once(NOW)["proposed"] == 1


@pytest.mark.parametrize(("mode", "calls"), [("on", 1), ("off", 0)])
def test_once_polls_in_the_planner_mode(engine, llm, capsys, monkeypatch, mode, calls):
    save_forecast(engine)
    monkeypatch.setenv("PLANNER_MODE", mode)
    get_settings.cache_clear()
    try:
        assert main(["once"], engine=engine, llm=llm) == 0
    finally:
        get_settings.cache_clear()

    assert json.loads(capsys.readouterr().out)["mode"] == mode
    assert len(llm.calls) == calls


def test_planner_mode_defaults_to_off(monkeypatch):
    monkeypatch.delenv("PLANNER_MODE", raising=False)
    get_settings.cache_clear()
    try:
        assert get_settings().planner_mode == "off"
    finally:
        get_settings.cache_clear()


def test_synth_reset_keeps_every_planner_row(engine, llm, capsys):
    save_forecast(engine)
    runner = PlannerRunner(llm, "on", engine=engine)
    runner.poll_once(NOW)
    PlannerRunner(FakeLLM(error=LLMError("timeout", "Slow.")), "on", engine=engine).poll_once(
        NOW
    )  # the proposal exists: skipped, so nothing more
    save_forecast(engine, key="b", at=NOW + timedelta(hours=1))
    PlannerRunner(FakeLLM(error=LLMError("timeout", "Slow.")), "on", engine=engine).poll_once(
        NOW + timedelta(hours=1)
    )
    main(["try", "--yes"], engine=engine, llm=llm)
    before = [(row.id, row.trigger, row.status) for row in planner_rows(engine)]
    assert len(before) == 3

    with Session(engine) as db:
        synth.reset(db)
        db.commit()

    assert [(row.id, row.trigger, row.status) for row in planner_rows(engine)] == before
