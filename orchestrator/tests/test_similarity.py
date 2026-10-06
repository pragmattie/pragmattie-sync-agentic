from datetime import datetime

import pytest
from sqlalchemy.orm import Session

from sdlc.db import Base
from sdlc.similarity import jaccard, similar_issues, tokens
from sdlc.tables import Issue

CREATED = datetime(2026, 9, 1, 9, 0)


@pytest.fixture
def db(sqlite_engine):
    Base.metadata.create_all(sqlite_engine)
    with Session(sqlite_engine) as session:
        yield session


def _issue(db, number, title, **fields):
    values = {"source": "synthetic", "number": number, "title": title, "created_at": CREATED}
    issue = Issue(**{**values, **fields})
    db.add(issue)
    db.flush()
    return issue


def test_tokens_drop_stopwords_and_short_words():
    assert tokens("Add support for the CSV import of 10 leads, v2") == {"csv", "import", "leads"}


def test_tokens_of_nothing_are_empty():
    assert tokens(None) == set()
    assert tokens("") == set()


def test_jaccard():
    assert jaccard({"a", "b"}, {"b", "c"}) == pytest.approx(1 / 3)
    assert jaccard(set(), {"b"}) == 0.0
    assert jaccard({"a"}, set()) == 0.0


def test_matches_on_the_title_only(db):
    _issue(db, 1, "Lead import from CSV")
    _issue(db, 2, "Forecast snapshots")
    result = similar_issues(db, "Import leads", "From a CSV file")
    assert [s.number for s in result] == [1]
    assert result[0].score == round(2 / 5, 3)  # {import, csv} of {import, leads, csv, file, lead}


def test_the_body_counts_only_its_first_500_characters(db):
    _issue(db, 1, "Webhook retries")
    assert similar_issues(db, "Something", "x " * 300 + "webhook") == []
    assert [s.number for s in similar_issues(db, "Something", "webhook")] == [1]


def test_best_first_and_ties_broken_by_number(db):
    _issue(db, 9, "Pipeline board drag")
    _issue(db, 4, "Pipeline board colours")
    _issue(db, 6, "Pipeline board drag drop")
    result = similar_issues(db, "Pipeline board drag drop", None)
    assert [s.number for s in result] == [6, 9, 4]
    tied = similar_issues(db, "Pipeline board", None)
    assert [s.number for s in tied] == [4, 9, 6]  # 2/3 each for 4 and 9, then 6 at 1/2
    _issue(db, 2, "Pipeline board drag")
    assert [s.number for s in similar_issues(db, "Pipeline board drag", None)][:2] == [2, 9]


def test_scores_are_rounded_to_three_places(db):
    _issue(db, 1, "Lead scoring rules")
    (match,) = similar_issues(db, "Lead scoring", None)
    assert match.score == 0.667


def test_excludes_the_issue_itself(db):
    _issue(db, 1, "Lead scoring rules")
    _issue(db, 2, "Lead scoring weights")
    assert [s.number for s in similar_issues(db, "Lead scoring", None, exclude_number=1)] == [2]


def test_filters_by_source(db):
    _issue(db, 1, "Lead scoring rules", source="synthetic")
    _issue(db, 2, "Lead scoring weights", source="github")
    result = similar_issues(db, "Lead scoring", None, source="github")
    assert [s.number for s in result] == [2]
    assert len(similar_issues(db, "Lead scoring", None)) == 2


def test_skips_issues_without_a_number_and_unrelated_ones(db):
    _issue(db, None, "Lead scoring rules")
    _issue(db, 3, "Seat billing")
    assert similar_issues(db, "Lead scoring", None) == []


def test_an_empty_query_gives_no_candidates(db):
    _issue(db, 1, "The and for")
    _issue(db, 2, "Lead scoring")
    assert similar_issues(db, "Add a fix for the", "") == []
    assert similar_issues(db, "", None) == []


def test_limit_and_fields(db):
    for number in range(1, 6):
        _issue(
            db,
            number,
            f"Lead import {number}",
            module="leads",
            type="bug",
            estimate_points=3,
            actual_days=2.5,
            state="closed",
        )
    result = similar_issues(db, "Lead import", None, limit=2)
    assert [s.number for s in result] == [1, 2]
    first = result[0]
    assert (first.title, first.module, first.type) == ("Lead import 1", "leads", "bug")
    assert (first.estimate_points, first.actual_days, first.state) == (3, 2.5, "closed")
