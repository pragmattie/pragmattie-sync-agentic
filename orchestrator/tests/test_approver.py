from datetime import datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from sdlc.approver import (
    POLICY,
    Approver,
    ApproverError,
    approve,
    describe_pending,
    find_pull_request,
    load_approvers,
    main,
    pending,
    request_approval,
    resolve_approver,
)
from sdlc.db import Base
from sdlc.tables import Approval, PullRequest

NOON = datetime(2026, 10, 6, 12, 0)
SIMULATED = Approver(
    id="simulated-second-human",
    name="Simulated second approver",
    controlled_by="geoff",
    applies_to_tiers=("T3",),
)


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    return sqlite_engine


@pytest.fixture
def db(engine):
    with Session(engine) as session:
        yield session


def _pr(db, number, source="github", state="open"):
    pr = PullRequest(
        number=number,
        source=source,
        external_id=f"pr-{number}",
        title=f"Change {number}",
        state=state,
        created_at=NOON - timedelta(days=1),
    )
    db.add(pr)
    db.flush()
    return pr


def _write(tmp_path, text):
    path = tmp_path / "approvers.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_the_committed_policy_loads_the_simulated_approver():
    assert load_approvers(POLICY) == (SIMULATED,)


def test_the_default_path_is_the_policies_folder():
    assert POLICY.parts[-2:] == ("policies", "approvers.yaml")
    assert "proposed" not in POLICY.parts


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("approvers: []", "approvers: must be a non-empty list"),
        ("approvers: [{id: a, controlled_by: me, applies_to_tiers: [T3]}]", "needs a name"),
        ("approvers: [{id: a, name: A, controlled_by: me, applies_to_tiers: [T9]}]", "T0"),
        ("approvers: [", "not valid YAML"),
    ],
)
def test_a_bad_file_is_refused(tmp_path, text, message):
    with pytest.raises(ApproverError, match=message):
        load_approvers(_write(tmp_path, text))


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(ApproverError, match="no approvers file"):
        load_approvers(tmp_path / "absent.yaml")


def test_resolve_picks_the_only_one_or_the_named_one():
    other = Approver("other", "Other", "geoff", ("T2",))
    assert resolve_approver((SIMULATED,)) == SIMULATED
    assert resolve_approver((SIMULATED, other), "other") == other
    with pytest.raises(ApproverError, match="several approvers \\(simulated-second-human, other"):
        resolve_approver((SIMULATED, other))
    with pytest.raises(ApproverError, match="No approver 'nobody'.*simulated-second-human"):
        resolve_approver((SIMULATED,), "nobody")


def test_a_request_is_pending_and_simulated(db):
    pr = _pr(db, 7)
    row = request_approval(db, pr, SIMULATED, "T3", reason="Migration", now=NOON)
    assert (row.source, row.status, row.tier) == ("simulated", "pending", "T3")
    assert row.reason == "Migration"
    assert (row.requested_at, row.decided_at) == (NOON, None)


def test_a_tier_the_approver_doesnt_cover_is_refused(db):
    with pytest.raises(ApproverError, match="covers only T3, not T2"):
        request_approval(db, _pr(db, 7), SIMULATED, "T2")


@pytest.mark.parametrize("state", ["merged", "closed"])
def test_a_pr_that_isnt_open_is_refused(db, state):
    with pytest.raises(ApproverError, match=f"is {state}, not open"):
        request_approval(db, _pr(db, 7, state=state), SIMULATED, "T3")


def test_a_second_request_is_refused(db):
    pr = _pr(db, 7)
    request_approval(db, pr, SIMULATED, "T3")
    with pytest.raises(ApproverError, match="already asked"):
        request_approval(db, pr, SIMULATED, "T3")


def test_approve_without_a_request_is_refused(db):
    with pytest.raises(ApproverError, match="never asked"):
        approve(db, _pr(db, 7), SIMULATED)


def test_approve_then_approve_again_is_refused(db):
    pr = _pr(db, 7)
    request_approval(db, pr, SIMULATED, "T3", now=NOON)
    row = approve(db, pr, SIMULATED, note="Checked the downgrade", now=NOON + timedelta(hours=1))
    assert (row.status, row.note, row.decided_at) == (
        "approved",
        "Checked the downgrade",
        NOON + timedelta(hours=1),
    )
    with pytest.raises(ApproverError, match="already approved"):
        approve(db, pr, SIMULATED)


def test_pending_lists_open_prs_oldest_first_and_leaves_out_merged_and_approved(db):
    newer, older, merged, approved = (_pr(db, n) for n in (1, 2, 3, 4))
    request_approval(db, newer, SIMULATED, "T3", now=NOON + timedelta(hours=2))
    request_approval(db, older, SIMULATED, "T3", now=NOON)
    request_approval(db, merged, SIMULATED, "T3", now=NOON)
    request_approval(db, approved, SIMULATED, "T3", now=NOON)
    approve(db, approved, SIMULATED)
    merged.state = "merged"
    db.flush()
    assert [row.pull_request.number for row in pending(db)] == [2, 1]


def test_the_ambiguous_number_needs_a_source(db):
    _pr(db, 7, source="github")
    _pr(db, 7, source="synthetic")
    with pytest.raises(ApproverError, match="more than one source \\(github, synthetic\\)"):
        find_pull_request(db, 7)
    assert find_pull_request(db, 7, "synthetic").source == "synthetic"
    with pytest.raises(ApproverError, match="No pull request #8"):
        find_pull_request(db, 8)


def test_describe_pending_says_it_is_simulated(db):
    assert describe_pending([]) == "No approvals are waiting for you."
    request_approval(db, _pr(db, 7), SIMULATED, "T3", reason="Migration", now=NOON)
    text = describe_pending(pending(db))
    assert text.splitlines() == [
        "1 approval waiting for you (simulated, so not real human approvals):",
        "  - #7 (github) Change 7 [T3, asked 2026-10-06 12:00 UTC]: Migration",
        "Approve one with: python -m sdlc.approver approve <pr> [--source SOURCE]",
    ]


def _cli(engine, *argv):
    return main(list(argv), engine=engine, policy=POLICY)


def _approvals(engine):
    with Session(engine) as db:
        rows = db.scalars(select(Approval).order_by(Approval.id))
        return [(row.status, row.tier, row.reason, row.note) for row in rows]


def test_the_cli_requests_lists_and_approves(engine, capsys):
    with Session(engine) as db:
        _pr(db, 7)
        _pr(db, 8)
        db.commit()
    assert _cli(engine, "pending") == 0
    assert capsys.readouterr().out == "No approvals are waiting for you.\n"

    assert _cli(engine, "request", "7", "--reason", "Migration") == 0
    assert _cli(engine, "request", "8", "--tier", "T3") == 0
    out = capsys.readouterr().out
    assert "Asked Simulated second approver to approve #8 (github) at T3." in out
    assert _cli(engine, "pending") == 0
    out = capsys.readouterr().out
    assert out.startswith("2 approvals waiting for you (simulated, so not real human approvals):")
    assert "#7 (github) Change 7" in out and "#8 (github) Change 8" in out

    assert _cli(engine, "approve", "7", "--note", "Looked at it") == 0
    assert "This is a simulated approval, not a real person's." in capsys.readouterr().out
    assert _approvals(engine) == [
        ("approved", "T3", "Migration", "Looked at it"),
        ("pending", "T3", None, None),
    ]


def test_the_cli_reports_a_refusal_and_writes_nothing(engine, capsys):
    with Session(engine) as db:
        _pr(db, 7, source="github")
        _pr(db, 7, source="synthetic")
        db.commit()
    assert _cli(engine, "request", "7") == 1
    assert "choose one with --source" in capsys.readouterr().err
    assert _cli(engine, "request", "7", "--source", "github", "--tier", "T2") == 1
    assert "covers only T3, not T2" in capsys.readouterr().err
    assert _cli(engine, "approve", "7", "--source", "github") == 1
    assert "never asked" in capsys.readouterr().err
    assert _cli(engine, "pending", "--approver", "nobody") == 1
    assert "No approver 'nobody'" in capsys.readouterr().err
    assert _approvals(engine) == []
