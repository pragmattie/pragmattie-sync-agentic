import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc import audit, issue_runner, release_runner, runner, suite_selector
from sdlc.db import Base
from sdlc.import_v1 import DRY_RUN, IMPORTED, main
from sdlc.suite_selector import RESULT
from sdlc.tables import AgentDecision, PullRequest
from sdlc.v1_export import DECISION_FIELDS

REPO = "pragmattie/pragmattie-sync-agentic"
T0 = datetime(2026, 10, 5, 9, 0)
SHA = "a" * 40


def _decision(id, minutes, agent="pr_risk", **fields):
    item = dict.fromkeys(DECISION_FIELDS)
    item.update(
        id=id,
        created_at=(T0 + timedelta(minutes=minutes)).isoformat(),
        agent=agent,
        agent_version="v1",
        subject_type="pr",
        subject_id=id - 5000,
        head_sha=SHA,
        attempt=1,
        trigger="poll",
        status="ok",
    )
    item.update(fields)
    return item


def _decisions():
    return [
        # Listed out of order: the importer sorts oldest first.
        _decision(
            5003,
            30,
            agent="tier_override",
            subject_id=1,
            trigger="human",
            human_override={"from": "T1", "to": "T2", "by": "owner"},
            supersedes_id=5001,
        ),
        _decision(
            5001,
            0,
            subject_id=1,
            model_id="rubric",
            prompt_version="p1",
            prompt_hash="h" * 64,
            inputs_digest={"additions": 100},
            raw_score=27,
            adjustment=3,
            final_score=30,
            tier="T1",
            signals={"change_size": 10},
            output={"floors": [], "reasons": ["Medium change."]},
            action_taken={"label": "tier:T1"},
            latency_ms=12,
        ),
        _decision(5002, 10, subject_id=1, attempt=2, status="error", error="timeout"),
        _decision(5004, 40, agent="implementer", subject_type="issue", subject_id=9),
        _decision(5005, 50, agent="reviewer", subject_id=1),
        _decision(5006, 60, subject_id=2, trigger="trial"),
        _decision(
            5007,
            70,
            agent="test_selector",
            subject_id=1,
            output={"suites": ["api"], "skipped": ["web"]},
        ),
        _decision(
            5008,
            80,
            agent="triage",
            subject_type="issue",
            subject_id=7,
            head_sha="c" * 12,
            output={"module": "Leads"},
        ),
    ]


def _approvals():
    return [
        {
            "pr": 8,
            "approver_id": "owner",
            "tier": "T3",
            "status": "approved",
            "reason": "Looks right.",
            "note": None,
            "requested_at": "2026-10-06T12:00:00",
            "decided_at": "2026-10-06T13:00:00",
        },
        {
            "pr": 9,
            "approver_id": None,
            "tier": "T3",
            "status": "pending",
            "reason": None,
            "note": "waiting",
            "requested_at": "2026-10-07T08:00:00",
            "decided_at": None,
        },
    ]


def _write(tmp_path, decisions=None, approvals=None, repository=REPO, name="export.json"):
    path = tmp_path / name
    data = {
        "exported_at": "2026-10-14T09:00:00Z",
        "repository": repository,
        "decisions": _decisions() if decisions is None else decisions,
        "approvals": _approvals() if approvals is None else approvals,
    }
    path.write_text(json.dumps(data), encoding="utf-8")
    return str(path)


@pytest.fixture
def engine(sqlite_engine, monkeypatch):
    monkeypatch.setenv("GITHUB_REPO", REPO)
    Base.metadata.create_all(sqlite_engine)
    return sqlite_engine


def _rows(engine) -> list[AgentDecision]:
    with Session(engine) as db:
        rows = list(db.scalars(select(AgentDecision).order_by(AgentDecision.id)))
        db.expunge_all()
        return rows


def _count(engine) -> int:
    with Session(engine) as db:
        return db.scalar(select(func.count()).select_from(AgentDecision))


def test_decisions_import_with_every_field_kept_and_source_v1(engine, tmp_path, capsys):
    assert main([_write(tmp_path), "--apply"], engine=engine) == 0

    rows = _rows(engine)
    assert {row.subject_source for row in rows} == {IMPORTED}
    risk = next(row for row in rows if row.agent == "pr_risk" and row.attempt == 1)
    source = next(d for d in _decisions() if d["id"] == 5001)
    for name in DECISION_FIELDS:
        if name in ("id", "created_at", "supersedes_id"):
            continue
        assert getattr(risk, name) == source[name], name
    assert risk.created_at == T0
    assert risk.id != 5001
    assert DRY_RUN not in capsys.readouterr().out


def test_rows_are_imported_oldest_first_and_supersedes_id_is_remapped(engine, tmp_path):
    main([_write(tmp_path), "--apply"], engine=engine)

    rows = [row for row in _rows(engine) if row.agent != "simulated_approver"]
    assert [row.created_at for row in rows] == sorted(row.created_at for row in rows)
    risk = next(row for row in rows if row.agent == "pr_risk" and row.attempt == 1)
    ruling = next(row for row in rows if row.agent == "tier_override")
    assert ruling.supersedes_id == risk.id
    assert ruling.human_override == {"from": "T1", "to": "T2", "by": "owner"}


def test_implementer_reviewer_and_trial_rows_are_left_out_and_counted(engine, tmp_path, capsys):
    main([_write(tmp_path), "--apply"], engine=engine)

    agents = [row.agent for row in _rows(engine)]
    assert "implementer" not in agents and "reviewer" not in agents
    assert all(row.trigger != "trial" for row in _rows(engine))
    out = capsys.readouterr().out
    assert "- implementer: 1 in the file, 0 imported, 0 skipped (already there), 1 left out" in out
    assert "- reviewer: 1 in the file, 0 imported, 0 skipped (already there), 1 left out" in out
    assert (
        "- pr_risk: 3 in the file, 2 imported, 0 skipped (already there), 1 left out; "
        "2026-10-05 09:00 UTC to 2026-10-05 10:00 UTC"
    ) in out


def test_approvals_become_simulated_approver_rows(engine, tmp_path, capsys):
    main([_write(tmp_path), "--apply"], engine=engine)

    approved, pending = [row for row in _rows(engine) if row.agent == "simulated_approver"]
    assert (approved.subject_type, approved.subject_source, approved.subject_id) == ("pr", "v1", 8)
    assert approved.head_sha == "approval-8-owner"
    assert (approved.trigger, approved.tier) == ("human", "T3")
    assert approved.status == "ok"
    assert approved.created_at == datetime(2026, 10, 6, 13, 0)
    assert approved.output == {
        "approver_id": "owner",
        "status": "approved",
        "reason": "Looks right.",
        "note": None,
        "requested_at": "2026-10-06T12:00:00",
        "decided_at": "2026-10-06T13:00:00",
    }
    assert (pending.status, pending.head_sha) == ("pending", "approval-9-")
    assert pending.created_at == datetime(2026, 10, 7, 8, 0)
    assert "- simulated_approver: 2 in the file, 2 imported" in capsys.readouterr().out


def test_a_second_import_adds_nothing(engine, tmp_path, capsys):
    path = _write(tmp_path)
    main([path, "--apply"], engine=engine)
    first = _count(engine)
    capsys.readouterr()

    assert main([path, "--apply"], engine=engine) == 0

    assert _count(engine) == first == 7
    out = capsys.readouterr().out
    assert "- pr_risk: 3 in the file, 0 imported, 2 skipped (already there), 1 left out" in out


def test_a_newer_export_adds_only_its_new_rows(engine, tmp_path):
    main([_write(tmp_path), "--apply"], engine=engine)
    before = {row.id for row in _rows(engine)}
    newer = [
        *_decisions(),
        _decision(5009, 90, agent="tier_override", subject_id=1, attempt=2, supersedes_id=5003),
    ]

    main([_write(tmp_path, decisions=newer, name="newer.json"), "--apply"], engine=engine)

    added = [row for row in _rows(engine) if row.id not in before]
    assert len(added) == 1
    old_ruling = next(
        row for row in _rows(engine) if row.agent == "tier_override" and row.attempt == 1
    )
    assert added[0].supersedes_id == old_ruling.id


def test_a_supersedes_id_pointing_at_a_left_out_row_is_none(engine, tmp_path):
    decisions = [_decision(5001, 0, agent="reviewer"), _decision(5002, 10, supersedes_id=5001)]
    main([_write(tmp_path, decisions=decisions, approvals=[]), "--apply"], engine=engine)

    (row,) = _rows(engine)
    assert row.supersedes_id is None


def test_the_dry_run_writes_nothing(engine, tmp_path, capsys):
    assert main([_write(tmp_path)], engine=engine) == 0

    assert _count(engine) == 0
    out = capsys.readouterr().out
    assert out.endswith(f"{DRY_RUN}\n")
    assert "- pr_risk: 3 in the file, 2 imported" in out


def test_a_wrong_repository_writes_nothing_and_says_why(engine, tmp_path, capsys):
    path = _write(tmp_path, repository="someone/else")

    assert main([path, "--apply"], engine=engine) == 1

    assert _count(engine) == 0
    assert capsys.readouterr().err == f"This export is for someone/else, not {REPO}.\n"


def test_a_decision_missing_a_field_writes_nothing_and_says_why(engine, tmp_path, capsys):
    decisions = _decisions()
    del decisions[-1]["tier"]

    assert main([_write(tmp_path, decisions=decisions), "--apply"], engine=engine) == 1

    assert _count(engine) == 0
    assert capsys.readouterr().err == "Decision 5008 is missing the field 'tier'.\n"


def test_an_approval_without_requested_at_writes_nothing_and_says_why(engine, tmp_path, capsys):
    approvals = _approvals()
    approvals[1]["requested_at"] = None

    assert main([_write(tmp_path, approvals=approvals), "--apply"], engine=engine) == 1

    assert _count(engine) == 0
    assert capsys.readouterr().err == "The approval for PR 9 is missing 'requested_at'.\n"


def test_approvals_by_different_approvers_on_one_pr_are_both_imported(engine, tmp_path):
    approvals = _approvals()
    approvals[1].update(pr=8, approver_id="a-very-long-approver-name-from-v1")

    main([_write(tmp_path, approvals=approvals), "--apply"], engine=engine)

    rows = [row for row in _rows(engine) if row.agent == "simulated_approver"]
    assert [row.head_sha for row in rows] == [
        "approval-8-owner",
        "approval-8-a-very-long-approver-name-fro",
    ]


def test_a_second_approval_by_the_same_approver_on_a_pr_writes_nothing(engine, tmp_path, capsys):
    approvals = [*_approvals(), {**_approvals()[0], "status": "rejected"}]

    assert main([_write(tmp_path, approvals=approvals), "--apply"], engine=engine) == 1

    assert _count(engine) == 0
    assert capsys.readouterr().err == "PR 8 has more than one approval by owner.\n"


# v2's own runners read source ``github`` only, so v1's rows on the same PRs change nothing.


def _own_rows(db, now):
    def record(agent, subject_type, subject_id, head_sha, **fields):
        return audit.record_decision(
            db,
            agent=agent,
            agent_version="2.0",
            subject_type=subject_type,
            subject_source="github",
            subject_id=subject_id,
            head_sha=head_sha,
            now=now,
            **{"trigger": "poll", **fields},
        )

    record("pr_risk", "pr", 1, SHA, tier="T1", final_score=25)
    ruling = {
        "comment_id": 1,
        "actor": "owner",
        "reason": "",
        "from_tier": "T1",
        "to_tier": "T2",
        "direction": "raise",
        "accepted": True,
        "why": "Raised.",
        "head_sha": SHA,
    }
    record("tier_override", "pr", 1, SHA, trigger="human", human_override=ruling)
    record("triage", "issue", 7, "c" * 12, output={"module": "Leads"})
    record("release_gate", "release", 1, SHA, output={"verdict": "pass"})
    record("test_selector", "pr", 1, SHA, output={"suites": ["api"], "skipped": ["web"]})
    record("test_selector", "pr", 2, SHA, output={"suites": ["api"], "skipped": ["web"]})
    record(
        "test_selector",
        "pr",
        2,
        SHA,
        attempt=2,
        trigger=RESULT,
        output={
            "suites": ["api"],
            "skipped": ["web"],
            "results": {"api": {"conclusion": "success", "flaky": False, "seconds": 60}},
            "missed": [],
            "caught": [],
            "saved_seconds": 60,
        },
    )
    db.commit()


def _what_runners_read(db, now):
    pr = PullRequest(source="github", number=1)
    latest = audit.latest_decision(
        db, "pr_risk", subject_type="pr", subject_source="github", subject_id=1
    )
    return {
        "risk": latest.id,
        "rulings": runner._rulings(db, pr),
        "triage": issue_runner._highest_attempt(db, 7, "c" * 12),
        "release": [row.id for row in release_runner._rows_for(db, SHA)],
        "unsettled": [row.id for row in suite_selector.unsettled(db, now)],
        "report": suite_selector.report(db),
    }


def test_v2s_runners_read_the_same_after_an_import(engine, tmp_path):
    now = T0 + timedelta(days=1)
    with Session(engine) as db:
        _own_rows(db, now - timedelta(hours=1))
        before = _what_runners_read(db, now)

    decisions = [
        *_decisions(),
        _decision(5010, 100, agent="release_gate", subject_type="release", subject_id=1),
        _decision(5011, 110, agent="test_selector", subject_id=3, trigger=RESULT),
    ]
    assert main([_write(tmp_path, decisions=decisions), "--apply"], engine=engine) == 0

    with Session(engine) as db:
        assert db.scalar(select(func.count()).where(AgentDecision.subject_source == IMPORTED)) == 9
        assert _what_runners_read(db, now) == before
    assert len(before["rulings"]) == 1 and before["unsettled"]
    assert before["report"]["commits"] == 1
