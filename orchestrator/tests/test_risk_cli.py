import re
from datetime import datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from sdlc.db import Base
from sdlc.risk import main
from sdlc.tables import Incident, PullRequest

THURSDAY = datetime(2026, 10, 1, 10, 0)
FRIDAY = datetime(2026, 10, 2, 10, 0)


def _pr(number, source="synthetic", merged=True, created_at=THURSDAY - timedelta(days=1), **fields):
    return PullRequest(
        number=number,
        source=source,
        title=f"PR {number}",
        review_count=1,
        state="merged" if merged else "open",
        created_at=created_at,
        merged_at=THURSDAY if merged else None,
        **fields,
    )


@pytest.fixture
def engine(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as db:
        db.add_all([_pr(number) for number in range(1, 10)])
        db.add(_pr(10, additions=900, caused_incident=True))
        db.add(_pr(11, merged=False, created_at=FRIDAY, touches_migration=True))
        db.add(_pr(1, source="github"))
        db.commit()
    return sqlite_engine


def _signals(out):
    """``{signal: (points, maximum)}`` from explain's output."""
    return {
        name: (int(points), int(maximum))
        for name, points, maximum in re.findall(r"^  (\w+)\s+(\d+) / (\d+)$", out, re.M)
    }


def _counts(engine):
    with Session(engine) as db:
        return tuple(
            db.scalar(select(func.count()).select_from(table)) for table in (PullRequest, Incident)
        )


@pytest.fixture
def unchanged(engine):
    before = _counts(engine)
    yield
    assert _counts(engine) == before


def test_explain_shows_each_signal_the_score_tier_and_reasons(engine, unchanged, capsys):
    assert main(["explain", "10"], engine=engine) == 0

    out = capsys.readouterr().out
    assert "PR #10 (synthetic)" in out
    signals = _signals(out)
    assert len(signals) == 10
    assert signals["change_size"] == (20, 20)
    assert signals["tests_with_change"] == (10, 10)
    assert signals["review_depth"] == (5, 5)
    assert signals["author_record"] == (5, 10)
    assert signals["timing"] == (0, 5)
    assert "Score: 40 / 100" in out
    assert "Tier: T1" in out
    assert "Risk score 40 is in the T1 band." in out


def test_explain_scores_an_open_pr_at_its_created_time(engine, unchanged, capsys):
    # Not merged, so it is timed when it was opened (a Friday), whatever today is.
    assert main(["explain", "11"], engine=engine) == 0

    out = capsys.readouterr().out
    assert _signals(out)["timing"] == (5, 5)
    assert _signals(out)["schema_migration"] == (15, 15)
    assert "Score: 25 / 100" in out
    assert "Tier: T3" in out
    assert "Floor 'schema_migration' sets a minimum of T3." in out


def test_explain_needs_source_for_a_number_in_both_sources(engine, unchanged, capsys):
    assert main(["explain", "1"], engine=engine) == 1

    err = capsys.readouterr().err
    assert "more than one source (github, synthetic)" in err
    assert "--source" in err


def test_explain_with_source_picks_one(engine, unchanged, capsys):
    assert main(["explain", "1", "--source", "github"], engine=engine) == 0

    assert "PR #1 (github)" in capsys.readouterr().out


def test_explain_reports_an_unknown_number(engine, unchanged, capsys):
    assert main(["explain", "99"], engine=engine) == 1

    assert "No pull request #99." in capsys.readouterr().err


def test_calibrate_grades_this_databases_history(engine, unchanged, capsys):
    assert main(["calibrate"], engine=engine) == 0

    out = capsys.readouterr().out
    assert "Merged PRs: 11   Incident PRs: 1" in out
    assert "Top decile: 2 PRs hold 1 of 1 incident PRs (100.00%)" in out
    assert out.rstrip().endswith("PASS")
    assert "FAIL" not in out


def test_calibrate_generated_pools_generated_histories(engine, unchanged, capsys):
    assert main(["calibrate", "--generated", "2"], engine=engine) == 0

    out = capsys.readouterr().out
    assert out.startswith("Histories: 2")
    assert "Bar 1" in out and "Bar 2" in out


def test_calibrate_generated_needs_at_least_one(engine, unchanged):
    with pytest.raises(SystemExit):
        main(["calibrate", "--generated", "0"], engine=engine)
