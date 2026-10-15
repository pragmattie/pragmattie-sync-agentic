from datetime import datetime, timedelta

import pytest
from sqlalchemy.orm import Session

from sdlc import cutover
from sdlc.audit import TRIAL, record_decision
from sdlc.db import Base
from sdlc.demo_check import FAIL, PASS, WARN
from sdlc.github_client import GitHubError
from sdlc.import_v1 import IMPORTED
from tests.fakes import FakeGitHub

NOW = datetime(2026, 10, 15, 9, 0)
SHA = "a" * 40


class UnreachableGitHub(FakeGitHub):
    def get(self, path: str, **params):
        self._log("GET", path)
        raise GitHubError(f"GitHub 404 on {path}: Not Found", status=404)


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    return sqlite_engine


def _row(engine, *, agent="pr_risk", number=7, sha=SHA, status=None, at=NOW, **fields):
    status = {"done": "risk-gate success"} if status is None else status
    with Session(engine) as db:
        record_decision(
            db,
            agent=agent,
            agent_version="1",
            subject_type="pr",
            subject_source=fields.pop("source", "github"),
            subject_id=number,
            trigger=fields.pop("trigger", "poll"),
            now=at,
            head_sha=sha,
            action_taken={"comment": {"done": "x"}, "status": status},
            **fields,
        )
        db.commit()


def _ready(engine) -> FakeGitHub:
    gh = FakeGitHub()
    gh.add_pr(7, SHA)
    _row(engine, at=NOW - timedelta(minutes=1))
    return gh


def _run(engine, gh, mode="enforce"):
    checks = cutover.run_checks(NOW, engine=engine, gh=gh, mode=mode)
    return {check.name: check for check in checks}


def test_all_good_passes_and_never_writes(engine, capsys, monkeypatch):
    gh = _ready(engine)
    checks = _run(engine, gh)
    assert all(check.state in (PASS, WARN) for check in checks.values())
    assert checks["Token: write statuses"].state == WARN
    assert "#7" in checks["v2's risk-gate"].detail
    assert ("GET", "/commits/main/statuses") in gh.requests

    monkeypatch.setattr(cutover, "utcnow", lambda: NOW)
    assert cutover.main(["check"], engine=engine, gh=gh, mode="enforce") == 0
    assert capsys.readouterr().out.endswith("v2 governs this repository.\n")
    assert gh.writes() == []


def test_identity_names_a_persons_login(engine):
    gh = _ready(engine)
    gh.as_person("geoffsmattie")
    check = _run(engine, gh)["Token: identity"]
    assert check.state == PASS
    assert check.detail == "comments are posted as geoffsmattie; v2 recognises them as its own"
    assert gh.requests.count(("GET", "/user")) == 1
    assert gh.writes() == []


def test_identity_of_an_app_token_is_a_bot(engine):
    gh = _ready(engine)  # /user answers 404, as for an installation token
    check = _run(engine, gh)["Token: identity"]
    assert check.state == PASS
    assert check.detail == "an app token: comments are posted by a bot"


def test_a_failure_exits_1(engine, capsys, monkeypatch):
    gh = _ready(engine)
    monkeypatch.setattr(cutover, "utcnow", lambda: NOW)
    assert cutover.main(["check"], engine=engine, gh=gh, mode="shadow") == 1
    assert "Not cut over: 1 check(s) failed." in capsys.readouterr().out


def test_unreachable_repository_fails(engine):
    gh = UnreachableGitHub()
    checks = _run(engine, gh)
    assert checks["Token: repository"].state == FAIL
    assert "404" in checks["Token: repository"].detail
    assert checks["v2's risk-gate"].state == FAIL
    assert gh.writes() == []


@pytest.mark.parametrize("mode", ["shadow", "observe", "off"])
def test_any_mode_but_enforce_fails(engine, mode):
    gh = _ready(engine)
    assert _run(engine, gh, mode)["ORCHESTRATOR_MODE"].state == FAIL
    assert gh.writes() == []


def test_stale_poll_fails(engine):
    gh = FakeGitHub()
    _row(engine, at=NOW - timedelta(minutes=6))
    check = _run(engine, gh)["Agents polling"]
    assert check.state == FAIL
    assert "6 min ago" in check.detail
    assert gh.writes() == []


def test_no_poll_fails_and_the_collector_imports_and_trials_dont_count(engine):
    gh = FakeGitHub()
    recent = NOW - timedelta(minutes=1)
    _row(engine, agent="forecaster", at=recent)
    _row(engine, number=8, source=IMPORTED, at=recent)
    _row(engine, number=9, trigger=TRIAL, at=recent)
    assert _run(engine, gh)["Agents polling"].state == FAIL
    assert gh.writes() == []


@pytest.mark.parametrize(
    "status", [{"skipped": "mode is observe"}, {"error": "risk-gate: GitHub 403"}]
)
def test_open_pr_whose_status_was_not_posted_fails(engine, status):
    gh = FakeGitHub()
    gh.add_pr(7, SHA)
    _row(engine, status=status, at=NOW - timedelta(minutes=1))
    check = _run(engine, gh)["v2's risk-gate"]
    assert check.state == FAIL
    assert "none was posted" in check.detail
    assert gh.writes() == []


def test_open_pr_with_no_row_for_its_head_fails(engine):
    gh = _ready(engine)
    gh.push(7, "b" * 40)
    check = _run(engine, gh)["v2's risk-gate"]
    assert check.state == FAIL
    assert "no pr_risk row" in check.detail


def test_v1s_imported_row_does_not_count(engine):
    gh = FakeGitHub()
    gh.add_pr(7, SHA)
    _row(engine, agent="triage", number=3, at=NOW - timedelta(minutes=1))
    _row(engine, source=IMPORTED)
    assert _run(engine, gh)["v2's risk-gate"].state == FAIL


def test_a_later_attempt_that_posted_counts(engine):
    gh = FakeGitHub()
    gh.add_pr(7, SHA)
    _row(engine, status={"skipped": "mode is observe"}, at=NOW - timedelta(hours=1))
    _row(engine, attempt=2, at=NOW - timedelta(minutes=1))
    assert _run(engine, gh)["v2's risk-gate"].state == PASS


def test_newest_open_pr_is_the_one_checked_and_drafts_are_skipped(engine):
    gh = _ready(engine)
    gh.add_pr(9, "c" * 40, draft=True)
    gh.add_pr(8, "b" * 40)
    check = _run(engine, gh)["v2's risk-gate"]
    assert check.state == FAIL
    assert "#8" in check.detail


def test_no_open_pr_passes_with_a_note(engine):
    gh = FakeGitHub()
    _row(engine, agent="triage", at=NOW - timedelta(minutes=1))
    checks = _run(engine, gh)
    assert checks["v2's risk-gate"].state == PASS
    assert checks["v2's risk-gate"].detail == "no open PR to check"
    assert all(check.state != FAIL for check in checks.values())
    assert gh.writes() == []
