import json
from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc import eval as eval_module
from sdlc import github_client
from sdlc.agents import triage
from sdlc.audit import record_decision
from sdlc.db import Base
from sdlc.eval import (
    BARS,
    EVAL_DIR,
    SETS,
    EvalSet,
    agent_answers,
    describe,
    grade,
    main,
    points_step,
    run_blind,
)
from sdlc.tables import AgentDecision, Issue
from tests.fakes import FakeLLM

FIXTURES = Path(__file__).parent / "fixtures" / "eval"
CREATED = datetime(2026, 5, 1, 9, 0)

# The agent's answer for each fixture issue, keyed by a word of its title.
ANSWERS = {
    "Import": {"module": "leads", "type": "feature", "priority": "p2", "estimate_points": 5},
    "Pipeline": {"module": "pipeline", "type": "bug", "priority": "p1", "estimate_points": 2},
    "Cache": {"module": "forecasting", "type": "feature", "priority": "p3", "estimate_points": 5},
}


class TitleLLM(FakeLLM):
    """Answers each issue according to the first word of its title."""

    def call(self, **kwargs):
        title = kwargs["user"].split("Title: ", 1)[1].split()[0]
        self.data = {
            "duplicate_of": 0,
            "confidence": 0.8,
            "rationale": "Sized like past work.",
            "questions": [],
            **ANSWERS[title],
        }
        return super().call(**kwargs)


def _label(number, module="leads", type="feature", points=3, priority=None):
    label = {"number": number, "module": module, "type": type, "points": points}
    if priority:
        label["priority"] = priority
    return label


def _answer(module="leads", type="feature", points=3, priority=None):
    return {"module": module, "type": type, "points": points, "priority": priority}


@pytest.fixture
def sets(monkeypatch):
    fixture = EvalSet(FIXTURES / "labels.json", FIXTURES / "issues.json", "synthetic")
    monkeypatch.setattr(eval_module, "SETS", {"backlog": fixture, "holdout": fixture})


@pytest.fixture
def no_github(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the eval must never talk to GitHub")

    monkeypatch.setattr(github_client.GitHubClient, "__init__", refuse)


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        db.add_all(
            [
                Issue(
                    source="synthetic",
                    number=11,
                    title="Lead import from CSV",
                    module="leads",
                    type="feature",
                    estimate_points=3,
                    actual_days=2.5,
                    state="closed",
                    created_at=CREATED,
                ),
                Issue(
                    source="synthetic",
                    number=12,
                    title="Pipeline stage totals rollup",
                    created_at=CREATED,
                ),
                # A real issue with labels: it must never be shown as an example.
                Issue(
                    source="github",
                    number=50,
                    title="Pipeline stage totals on the board",
                    module="pipeline",
                    type="bug",
                    estimate_points=2,
                    created_at=CREATED,
                ),
            ]
        )
        db.commit()
    return sqlite_engine


def _decide(db, number, output, *, trigger="opened", status="ok", source="github", now=CREATED):
    return record_decision(
        db,
        agent=triage.AGENT,
        agent_version=triage.AGENT_VERSION,
        subject_type="issue",
        subject_source=source,
        subject_id=number,
        trigger=trigger,
        status=status,
        head_sha=f"v{now.day}{trigger}{status}{source}",
        now=now,
        output=output,
    )


def _trials(engine):
    with Session(engine) as db:
        return list(db.scalars(select(AgentDecision).order_by(AgentDecision.id)))


# The sets


def test_both_sets_are_v1s_issues_recorded_as_simulated():
    assert SETS == {
        "backlog": EvalSet(
            EVAL_DIR / "triage_eval_set.json", EVAL_DIR / "_issues.json", "synthetic"
        ),
        "holdout": EvalSet(
            EVAL_DIR / "triage_holdout_set.json", EVAL_DIR / "holdout_issues.json", "synthetic"
        ),
    }


# points_step


def test_points_step_counts_steps_in_the_scale():
    assert points_step(3, 5) == 1
    assert points_step(3, 8) == 2
    assert points_step(8, 1) == 4
    assert points_step(2, 2) == 0


# grade


def test_grade_gives_exact_rates():
    labels = [_label(1), _label(2), _label(3, module="pipeline"), _label(4, type="bug")]
    answers = {1: _answer(), 2: _answer(), 3: _answer(), 4: _answer(points=5)}
    report = grade(labels, answers)
    assert report["rates"]["module"] == 0.75
    assert report["rates"]["type"] == 0.75
    assert report["rates"]["points"] == 0.75
    assert report["rates"]["points_within_one"] == 1.0
    assert report["rates"]["priority"] is None  # nothing labelled
    assert report["issues"] == 4


def test_points_within_one_step_passes_3_vs_5_and_fails_3_vs_8():
    report = grade([_label(1), _label(2)], {1: _answer(points=5), 2: _answer(points=8)})
    assert [row["match"]["points_within_one"] for row in report["rows"]] == [True, False]
    assert [row["match"]["points"] for row in report["rows"]] == [False, False]
    assert report["rates"]["points_within_one"] == 0.5


def test_a_missing_answer_counts_as_a_miss():
    report = grade(
        [_label(1, priority="p2"), _label(2, priority="p2")], {1: _answer(priority="p2")}
    )
    assert report["missing"] == [2]
    for key in ("module", "type", "points", "points_within_one", "priority"):
        assert report["rates"][key] == 0.5
    assert report["rows"][1]["agent"] is None
    assert report["patterns"] == []  # a missing answer is not a disagreement


def test_priority_is_graded_only_where_labelled_and_never_gated():
    labels = [_label(1, priority="p1"), _label(2)]
    report = grade(labels, {1: _answer(priority="p3"), 2: _answer(priority="p3")})
    assert report["rates"]["priority"] == 0.0
    assert report["rows"][1]["match"]["priority"] is None
    assert "priority" not in report["bars"]
    assert report["passed"] is True


def test_patterns_are_grouped_and_ordered_most_frequent_first():
    labels = [
        _label(1, type="chore"),
        _label(2, type="chore"),
        _label(3, type="chore", points=2),
        _label(4, module="pipeline"),
    ]
    answers = {
        1: _answer(),
        2: _answer(),
        3: _answer(),
        4: _answer(),
    }
    patterns = grade(labels, answers)["patterns"]
    assert patterns == [
        {
            "dimension": "type",
            "person": "chore",
            "agent": "feature",
            "count": 3,
            "issues": [1, 2, 3],
        },
        {"dimension": "module", "person": "pipeline", "agent": "leads", "count": 1, "issues": [4]},
        {"dimension": "points", "person": 2, "agent": 3, "count": 1, "issues": [3]},
    ]


def _with_misses(key, hits, total=20):
    labels = [_label(n) for n in range(total)]
    wrong = {"module": {"module": "pipeline"}, "type": {"type": "bug"}, "points": {"points": 8}}
    answers = {n: _answer() if n < hits else _answer(**wrong[key]) for n in range(total)}
    return grade(labels, answers)


@pytest.mark.parametrize(
    ("key", "bar_key", "edge"),
    [("module", "module", 17), ("type", "type", 18), ("points", "points_within_one", 14)],
)
def test_each_bar_passes_at_its_edge_and_fails_just_below(key, bar_key, edge):
    assert BARS[bar_key] == edge / 20
    at = _with_misses(key, edge)
    assert at["bars"][bar_key] == {"rate": edge / 20, "bar": BARS[bar_key], "passed": True}
    below = _with_misses(key, edge - 1)
    assert below["bars"][bar_key]["passed"] is False
    assert below["passed"] is False


def test_the_bars_are_fixed():
    assert BARS == {"module": 0.85, "type": 0.90, "points_within_one": 0.70}


def test_describe_shows_each_rate_against_its_bar_then_patterns():
    report = grade([_label(1, priority="p2"), _label(2, type="chore")], {1: _answer(priority="p1")})
    text = describe(report)
    assert "Module: 50% (bar 85%) FAIL" in text
    assert "Type: 50% (bar 90%) FAIL" in text
    assert "Points within one step: 50% (bar 70%) FAIL" in text
    assert "Priority: 0% (reported, not gated)" in text
    assert "Missing answers (counted as misses): #2" in text
    assert "1x priority: p2 -> p1 (#1)" in text
    passing = describe(grade([_label(1)], {1: _answer(points=5)}))
    assert "Module: 100% (bar 85%) PASS" in passing
    assert "1x points: 3 -> 5 (#1)" in passing
    assert passing.index("Priority") < passing.index("Patterns")
    assert "No disagreements." in describe(grade([_label(1)], {1: _answer()}))


# agent_answers


def test_agent_answers_takes_the_latest_ok_non_trial_github_decision(engine):
    with Session(engine) as db:
        out = {"module": "leads", "type": "feature", "priority": "p2", "estimate_points": 3}
        _decide(db, 1, {**out, "module": "accounts"}, now=datetime(2026, 9, 1, 9, 0))
        _decide(db, 1, out, now=datetime(2026, 9, 2, 9, 0))
        _decide(db, 1, {**out, "type": "bug"}, trigger="trial", now=datetime(2026, 9, 3, 9, 0))
        _decide(db, 1, {}, status="timeout", now=datetime(2026, 9, 4, 9, 0))
        _decide(db, 2, out, source="synthetic")
        assert agent_answers(db) == {
            1: {"module": "leads", "type": "feature", "points": 3, "priority": "p2"}
        }


# run_blind


def test_without_yes_nothing_is_called_and_the_cost_is_shown(engine, sets, no_github):
    llm = TitleLLM()
    with Session(engine) as db:
        preview = run_blind(db, llm, "backlog", yes=False)
    assert llm.calls == []
    assert preview["calls"] == 3
    assert preview["max_cost_usd"] > 0
    assert [issue["number"] for issue in preview["issues"]] == [101, 102, 103]
    assert preview["issues"][0]["title"] == "Import leads from a CSV file"
    assert _trials(engine) == []


def test_a_blind_run_shows_no_labels_and_only_simulated_examples(engine, sets, no_github):
    llm = TitleLLM()
    with Session(engine) as db:
        result = run_blind(db, llm, "backlog", yes=True)
    assert len(llm.calls) == 3
    for call in llm.calls:
        user = call["user"]
        assert "Existing labels: (none)" in user
        assert "regression" not in user  # a label note
        assert "#50" not in user  # a real, labelled issue
    assert "#11 [closed]" in llm.calls[0]["user"]
    assert "#12 [" in llm.calls[1]["user"]
    assert result["answers"][102] == {
        "module": "pipeline",
        "type": "bug",
        "points": 2,
        "priority": "p1",
    }


def test_every_candidate_comes_from_simulated_history(engine, sets, monkeypatch):
    seen = []
    real = triage.similar_issues

    def spy(*args, **kwargs):
        seen.append(kwargs["source"])
        return real(*args, **kwargs)

    monkeypatch.setattr(triage, "similar_issues", spy)
    with Session(engine) as db:
        run_blind(db, TitleLLM(), "backlog", yes=True)
        run_blind(db, TitleLLM(), "backlog", yes=False)
    assert seen == ["synthetic"] * 6


@pytest.mark.parametrize("set_name", ["backlog", "holdout"])
def test_one_trial_row_per_issue_with_the_sets_source(engine, sets, no_github, set_name):
    with Session(engine) as db:
        run_blind(db, TitleLLM(), set_name, yes=True)
        run_blind(db, TitleLLM(), set_name, yes=True)  # a second run never collides
    rows = _trials(engine)
    assert len(rows) == 6
    assert {(row.trigger, row.subject_source, row.status) for row in rows} == {
        ("trial", "synthetic", "ok")
    }
    assert [row.subject_id for row in rows] == [101, 102, 103] * 2
    assert [row.attempt for row in rows] == [4, 4, 4, 5, 5, 5]
    with Session(engine) as db:
        assert agent_answers(db) == {}  # trial rows never count as stored decisions


def test_a_failed_call_is_recorded_and_graded_as_missing(engine, sets):
    from sdlc.agents.llm import LLMError

    llm = FakeLLM(error=LLMError("timeout", "slow"))
    with Session(engine) as db:
        result = run_blind(db, llm, "backlog", yes=True)
    assert result["answers"] == {}
    assert {row.status for row in _trials(engine)} == {"timeout"}


# The CLI


@pytest.mark.parametrize("argv", [[], ["--fresh"], ["--set", "holdout"]])
def test_cli_without_yes_shows_what_it_would_send_and_cost(engine, sets, capsys, argv):
    with Session(engine) as db:
        _decide(db, 101, {"module": "leads", "type": "feature", "estimate_points": 3})
        db.commit()
    llm = TitleLLM()
    assert main(argv, engine=engine, llm=llm) == 0
    out = capsys.readouterr().out
    name = "holdout" if "holdout" in argv else "backlog"
    assert f"A blind run of the {name} set makes 3 real, paid model calls" in out
    assert "#101 Import leads from a CSV file" in out
    assert "#103 Cache the forecast rollup" in out
    assert "Graded" not in out  # stored decisions are never graded
    assert llm.calls == []
    assert len(_trials(engine)) == 1  # only the stored decision


def test_cli_json_without_yes_prints_the_preview(engine, sets, capsys):
    llm = TitleLLM()
    assert main(["--json"], engine=engine, llm=llm) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["set"] == "backlog"
    assert preview["calls"] == 3 and preview["max_cost_usd"] > 0
    assert [issue["number"] for issue in preview["issues"]] == [101, 102, 103]
    assert llm.calls == []
    assert _trials(engine) == []


@pytest.mark.parametrize("argv", [["--yes"], ["--fresh", "--yes"]])
def test_cli_yes_runs_blind_and_grades_those_answers(engine, sets, no_github, capsys, argv):
    assert main([*argv, "--json"], engine=engine, llm=TitleLLM()) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["set"] == "backlog"
    assert report["calls"] == 3
    assert report["rates"]["module"] == 1.0
    assert report["rates"]["type"] == pytest.approx(2 / 3)
    assert report["rates"]["points_within_one"] == 1.0
    assert report["patterns"][0]["dimension"] == "type"
    assert {row.subject_source for row in _trials(engine)} == {"synthetic"}
    assert len(_trials(engine)) == 3


def test_cli_yes_prints_the_graded_report(engine, sets, no_github, capsys):
    assert main(["--set", "holdout", "--yes"], engine=engine, llm=TitleLLM()) == 0
    out = capsys.readouterr().out
    assert "Graded 3 issues." in out
    assert "Type: 67% (bar 90%) FAIL" in out
    assert "1x type: chore -> feature (#103)" in out
    assert "Nothing was written to GitHub." in out
