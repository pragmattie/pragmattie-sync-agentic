from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from sdlc.agent_runs import WORKFLOW_BOT, parse_review, parse_run, record_review, record_run
from sdlc.audit import list_decisions
from sdlc.db import Base

# The build run's comment on #44, as implement.yml wrote it.
RUN_COMMENT = (
    "Agent run (build, `claude-opus-5-5`, success): 14 turns, $0.51. "
    "[Run log](https://github.com/pragmattie/pragmattie-sync-agentic/actions/runs/37306331835)\n"
    "\n"
    '<!-- pragmattie-run {"run":"37306331835","action":"build","issue":44,"pr":"158",'
    '"tier":"T3","model":"claude-opus-5-5","outcome":"success","tests_passed":"true",'
    '"error":"","turns":14,"input_tokens":22,"output_tokens":12089,'
    '"cache_read_tokens":316153,"cache_write_tokens":39985,"cost_usd":0.5082} -->\n'
)
RUN_URL = "https://github.com/pragmattie/pragmattie-sync-agentic/issues/44#issuecomment-5994009854"

# The plan run's comment on #44: no pull request yet, and no tests to run.
PLAN_COMMENT = (
    "Agent run (plan, `claude-opus-5-5`, success): 6 turns, $0.17. "
    "[Run log](https://github.com/pragmattie/pragmattie-sync-agentic/actions/runs/37236893235)\n"
    "\n"
    '<!-- pragmattie-run {"run":"37236893235","action":"plan","issue":44,"pr":"","tier":"T3",'
    '"model":"claude-opus-5-5","outcome":"success","tests_passed":"n/a","error":"","turns":6,'
    '"input_tokens":8,"output_tokens":1684,"cache_read_tokens":67565,'
    '"cache_write_tokens":24663,"cost_usd":0.173} -->\n'
)

# The reviewer's first comment on #162, shortened to its header, one finding and its record.
REVIEW_COMMENT = (
    "## AI review (shadow): would request changes\n"
    "\n"
    "_Advisory only: a person decides. Reviewed commit `14bd0900` against #46._\n"
    "\n"
    "**Findings**\n"
    "\n"
    "- **blocker** `orchestrator/tests/test_calibration.py:92`: The test passes whatever the "
    "tie-break is, so the acceptance criterion that it checks the tie-break is not met.\n"
    "\n"
    "<sub>claude-opus-5-5 · 10 turns · $0.34 · "
    "[run log](https://github.com/pragmattie/pragmattie-sync-agentic/actions/runs/37346478293)"
    "</sub>\n"
    "\n"
    '<!-- pragmattie-review {"run":"37346478293","pr":162,"issue":46,'
    '"sha":"14bd0900bdda834c8d2609b5c614a96399b37dd7","model":"claude-opus-5-5",'
    '"outcome":"success","verdict":"request_changes","blockers":1,"should_fix":0,'
    '"criteria_met":10,"criteria":11,"turns":10,"input_tokens":16,"output_tokens":5020,'
    '"cache_read_tokens":229525,"cache_write_tokens":37505,"cost_usd":0.3375} -->\n'
)
REVIEW_URL = (
    "https://github.com/pragmattie/pragmattie-sync-agentic/pull/162#issuecomment-5999358133"
)

# The reviewer's first comment on #158, shortened the same way.
APPROVE_COMMENT = (
    "## AI review (shadow): would approve\n"
    "\n"
    "_Advisory only: a person decides. Reviewed commit `fc0571ce` against #44._\n"
    "\n"
    "The loader, the dataclasses and the tests match the spec.\n"
    "\n"
    "<sub>claude-opus-5-5 · 8 turns · $0.30 · "
    "[run log](https://github.com/pragmattie/pragmattie-sync-agentic/actions/runs/37306689231)"
    "</sub>\n"
    "\n"
    '<!-- pragmattie-review {"run":"37306689231","pr":158,"issue":44,'
    '"sha":"fc0571ce524be191ff1b53b88a6d40c450947ded","model":"claude-opus-5-5",'
    '"outcome":"success","verdict":"approve","blockers":0,"should_fix":2,"criteria_met":6,'
    '"criteria":8,"turns":8,"input_tokens":12,"output_tokens":4655,'
    '"cache_read_tokens":149790,"cache_write_tokens":34482,"cost_usd":0.2992} -->\n'
)


def _comment(url, created_at="2026-10-04T15:21:07Z"):
    return {"html_url": url, "created_at": created_at, "user": {"login": WORKFLOW_BOT}}


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def test_workflow_bot_is_github_actions():
    assert WORKFLOW_BOT == "github-actions[bot]"


def test_parse_run_reads_the_record_inside_the_comment():
    record = parse_run(RUN_COMMENT)

    assert record["run"] == "37306331835"
    assert record["issue"] == 44
    assert record["pr"] == "158"
    assert record["cache_read_tokens"] == 316153


def test_parse_review_reads_the_record_inside_the_comment():
    record = parse_review(REVIEW_COMMENT)

    assert record["pr"] == 162
    assert record["sha"] == "14bd0900bdda834c8d2609b5c614a96399b37dd7"
    assert record["verdict"] == "request_changes"


@pytest.mark.parametrize(
    "body",
    [
        None,
        "",
        "Agent run (build): looks good.",
        RUN_COMMENT.replace('"issue":44,', '"issue":44,,'),
        RUN_COMMENT.replace('cost_usd":0.5082}', 'cost_usd":0.5082'),
        RUN_COMMENT.replace('"issue":44,', ""),
        RUN_COMMENT.replace('"issue":44', '"issue":"44"'),
        RUN_COMMENT.replace('"issue":44', '"issue":true'),
        RUN_COMMENT.replace('"run":"37306331835",', ""),
        RUN_COMMENT.replace('{"run"', '["run"').replace("0.5082}", "0.5082]"),
        REVIEW_COMMENT,
    ],
    ids=[
        "none",
        "empty",
        "no-record",
        "broken-json",
        "unclosed-json",
        "missing-issue",
        "issue-not-int",
        "issue-bool",
        "missing-run",
        "not-an-object",
        "review-record",
    ],
)
def test_parse_run_rejects_a_missing_or_invalid_record(body):
    assert parse_run(body) is None


@pytest.mark.parametrize(
    "body",
    [
        REVIEW_COMMENT.replace('"pr":162,', ""),
        REVIEW_COMMENT.replace('"run":"37346478293",', ""),
        REVIEW_COMMENT.replace('"pr":162,', '"pr":162'),
        RUN_COMMENT,
    ],
    ids=["missing-pr", "missing-run", "broken-json", "run-record"],
)
def test_parse_review_rejects_a_missing_or_invalid_record(body):
    assert parse_review(body) is None


def test_record_run_adds_one_implementer_row(db):
    assert record_run(db, parse_run(RUN_COMMENT), _comment(RUN_URL), "github") is True
    db.commit()

    [row] = list_decisions(db)
    assert row.agent == "implementer"
    assert (row.subject_type, row.subject_source, row.subject_id) == ("issue", "github", 44)
    assert row.head_sha == "run-37306331835"
    assert row.attempt == 1
    assert row.trigger == "workflow"
    assert row.model_id == "claude-opus-5-5"
    assert row.tier == "T3"
    assert (row.input_tokens, row.output_tokens) == (22, 12089)
    assert row.output == parse_run(RUN_COMMENT)
    assert row.action_taken == {"action": "build", "pr": "158", "comment": RUN_URL}
    assert row.status == "ok"
    assert row.error is None
    assert row.created_at == datetime(2026, 10, 4, 15, 21, 7)


def test_record_run_without_tests_is_ok(db):
    assert record_run(db, parse_run(PLAN_COMMENT), _comment(RUN_URL), "github") is True

    [row] = list_decisions(db)
    assert row.status == "ok"
    assert row.action_taken["pr"] == ""


def test_record_run_skips_a_run_already_recorded(db):
    record = parse_run(RUN_COMMENT)
    assert record_run(db, record, _comment(RUN_URL), "github") is True
    db.commit()

    assert record_run(db, record, _comment(RUN_URL), "github") is False
    assert len(list_decisions(db)) == 1
    assert record_run(db, record, _comment(RUN_URL), "synthetic") is True


def test_record_run_with_failing_tests_is_an_error(db):
    body = RUN_COMMENT.replace('"tests_passed":"true"', '"tests_passed":"false"').replace(
        '"error":""', '"error":"3 tests failed in tests/test_audit.py"'
    )

    record_run(db, parse_run(body), _comment(RUN_URL), "github")

    [row] = list_decisions(db)
    assert row.status == "error"
    assert row.error == "3 tests failed in tests/test_audit.py"


def test_record_run_with_failing_tests_and_no_error_text_says_tests_failed(db):
    body = RUN_COMMENT.replace('"tests_passed":"true"', '"tests_passed":"false"')

    record_run(db, parse_run(body), _comment(RUN_URL), "github")

    [row] = list_decisions(db)
    assert row.status == "error"
    assert row.error == "Tests failed."


def test_record_run_with_a_failed_outcome_is_an_error(db):
    body = RUN_COMMENT.replace('"outcome":"success"', '"outcome":"failure"')

    record_run(db, parse_run(body), _comment(RUN_URL), "github")

    [row] = list_decisions(db)
    assert row.status == "error"
    assert row.error == "Outcome 'failure'."


def test_record_run_ignores_a_tier_outside_t0_to_t3(db):
    body = RUN_COMMENT.replace('"tier":"T3"', '"tier":"none"')

    record_run(db, parse_run(body), _comment(RUN_URL), "github")

    assert list_decisions(db)[0].tier is None


def test_record_review_adds_one_reviewer_row(db):
    assert record_review(db, parse_review(REVIEW_COMMENT), _comment(REVIEW_URL), "github") is True
    db.commit()

    [row] = list_decisions(db)
    assert row.agent == "reviewer"
    assert (row.subject_type, row.subject_source, row.subject_id) == ("pr", "github", 162)
    assert row.head_sha == "review-37346478293"
    assert row.trigger == "workflow"
    assert row.model_id == "claude-opus-5-5"
    assert (row.input_tokens, row.output_tokens) == (16, 5020)
    assert row.output == parse_review(REVIEW_COMMENT)
    assert row.action_taken == {
        "verdict": "request_changes",
        "commit": "14bd0900bdda834c8d2609b5c614a96399b37dd7",
        "comment": REVIEW_URL,
    }
    assert row.status == "ok"


def test_record_review_approve_is_ok(db):
    record_review(db, parse_review(APPROVE_COMMENT), _comment(REVIEW_URL), "github")

    [row] = list_decisions(db)
    assert row.status == "ok"
    assert row.action_taken["verdict"] == "approve"
    assert row.action_taken["commit"] == "fc0571ce524be191ff1b53b88a6d40c450947ded"


def test_record_review_skips_a_run_already_recorded(db):
    record = parse_review(REVIEW_COMMENT)
    assert record_review(db, record, _comment(REVIEW_URL), "github") is True
    db.commit()

    assert record_review(db, record, _comment(REVIEW_URL), "github") is False
    assert len(list_decisions(db)) == 1


def test_record_review_with_an_invalid_verdict_is_an_error(db):
    body = REVIEW_COMMENT.replace('"verdict":"request_changes"', '"verdict":"invalid"')

    record_review(db, parse_review(body), _comment(REVIEW_URL), "github")

    [row] = list_decisions(db)
    assert row.status == "error"
    assert row.error == "Verdict 'invalid'."
