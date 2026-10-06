from sdlc.agents.llm import LLMResult
from sdlc.agents.triage import Assessment
from sdlc.agents.triage_comment import (
    MARKER,
    Meta,
    comment_version,
    content_version,
    render,
)
from sdlc.similarity import SimilarIssue

RESULT = LLMResult(
    data={},
    model="claude-haiku-4-5-20251001",
    input_tokens=800,
    output_tokens=120,
    cache_read_tokens=0,
    cache_write_tokens=0,
    latency_ms=600,
    request_id="req_1",
    cost_usd=0.0014,
)


def _similar(number: int) -> SimilarIssue:
    return SimilarIssue(
        number=number,
        title=f"Lead import {number}",
        module="leads",
        type="feature",
        estimate_points=3,
        actual_days=2.0,
        state="closed",
        score=0.5,
    )


def _assessment(**extra) -> Assessment:
    fields = {
        "module": "leads",
        "type": "feature",
        "priority": "p2",
        "estimate_points": 3,
        "duplicate_of": None,
        "confidence": 0.82,
        "needs_info": False,
        "status": "ok",
        "error": None,
        "rationale": "Lead import change, sized like past import work.",
        "questions": [],
        "similar": [],
        "llm": RESULT,
        "model": RESULT.model,
        **extra,
    }
    return Assessment(**fields)


def test_content_version_ignores_labels_and_changes_with_the_title():
    version = content_version("Import leads", "From a CSV file")
    assert len(version) == 12 and int(version, 16) >= 0
    assert content_version("Import leads", "From a CSV file") == version  # labels aren't inputs
    assert content_version("Import leads from CSV", "From a CSV file") != version
    assert content_version("Import leads", "From an XLSX file") != version
    assert content_version("Import leads", None) == content_version("Import leads", "")


def test_a_failed_run_says_it_will_try_again():
    failed = _assessment(
        module=None,
        type=None,
        priority=None,
        estimate_points=None,
        confidence=None,
        status="timeout",
        error="took too long",
        rationale=None,
        llm=None,
    )
    body = render(failed, Meta(7, "abc123def456"))
    assert body.startswith(f"{MARKER}\n<!-- version:abc123def456 -->\n## Triage\n")
    assert "**Could not triage this issue yet** (timeout). It will try again shortly." in body
    assert "Estimate" not in body
    assert "decision 7" in body
    assert comment_version(body) == "abc123def456"


def test_a_full_run_shows_the_classification_duplicate_questions_and_similar_issues():
    assessment = _assessment(
        duplicate_of=12,
        needs_info=True,
        questions=["Which file formats?", "Roughly how many rows?"],
        similar=[_similar(n) for n in range(10, 17)],
    )
    body = render(assessment, Meta(42, "0123456789ab"))
    assert body.startswith(MARKER)
    assert "## Triage: leads / feature (p2)" in body
    assert "**Estimate 3 points**, confidence 82%." in body
    assert "Lead import change, sized like past import work." in body
    assert "Possibly a duplicate of #12 — linked, not closed." in body
    assert (
        "**Before this is ready to work on:**\n- Which file formats?\n- Roughly how many rows?"
        in (body)
    )
    assert "<details><summary>Similar past issues</summary>" in body
    assert "- #14 Lead import 14 (closed)" in body
    assert "#15" not in body  # five at most
    assert "left as-is" not in body
    assert body.rstrip().endswith(
        "<sub>decision 42 · claude-haiku-4-5-20251001 · 920 tokens · PragMattie Sync demo</sub>"
    )
    assert comment_version(body) == "0123456789ab"


def test_overrides_are_listed_and_nothing_optional_shows_without_cause():
    body = render(_assessment(), Meta(3, "0123456789ab", ("module", "priority")))
    assert "_A human has already set module, priority; left as-is._" in body
    assert "duplicate" not in body
    assert "Before this is ready" not in body
    assert "Similar past issues" not in body


def test_comment_version_is_none_without_a_marker():
    assert comment_version(None) is None
    assert comment_version("a person's comment") is None
