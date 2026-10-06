import pytest

from sdlc.agents.github_effects import TIER_COLOURS, Effects
from tests.fakes import BOT, PERSON, FakeGitHub

SHA = "a" * 40
MARKER = "<!-- marker -->"


@pytest.fixture
def gh():
    fake = FakeGitHub()
    fake.add_pr(5, SHA)
    return fake


def test_off_mode_writes_nothing_and_says_so(gh):
    effects = Effects(gh, "off")
    results = [
        effects.upsert_comment(5, MARKER, "hello"),
        effects.set_status(SHA, "pending", "waiting"),
        effects.set_tier_label(5, "T1"),
        effects.set_dimension_label(5, "module", "leads"),
        effects.add_label_if_absent(5, "triaged"),
        effects.remove_label(5, "triaged"),
    ]
    assert results == [{"skipped": "mode is off"}] * 6
    assert gh.requests == []


def test_reads_work_in_off_mode(gh):
    effects = Effects(gh, "off")
    assert effects.read_pr(5)["number"] == 5
    assert effects.read_diff(5).startswith("diff --git")
    assert effects.read_files(5) == [{"filename": "apps/crm-web/src/App.vue"}]
    assert effects.read_reviews(5) == []
    assert effects.read_labels(5) == []
    assert effects.read_issue(5)["number"] == 5
    assert effects.read_comments(5) == []


def test_upsert_comment_adds_then_edits_the_bots_comment(gh):
    effects = Effects(gh, "shadow")
    assert effects.upsert_comment(5, MARKER, f"{MARKER}\none") == {"done": "comment"}
    assert effects.upsert_comment(5, MARKER, f"{MARKER}\ntwo") == {"done": "comment"}
    assert [c["body"] for c in gh.comments] == [f"{MARKER}\ntwo"]
    assert gh.writes() == [
        ("POST", "/issues/5/comments"),
        ("PATCH", f"/issues/comments/{gh.comments[0]['id']}"),
    ]


def test_find_comment_ignores_a_persons_copy_of_the_marker(gh):
    gh.comment(5, f"{MARKER}\n- [x] fake", user=PERSON)
    effects = Effects(gh, "enforce")
    assert effects.find_comment(5, MARKER) is None
    mine = gh.comment(5, f"{MARKER}\nreal", user=BOT)
    assert effects.find_comment(5, MARKER)["id"] == mine["id"]


def test_set_status_cuts_the_description_to_140(gh):
    effects = Effects(gh, "enforce")
    assert effects.set_status(SHA, "pending", "x" * 200) == {"done": "risk-gate pending"}
    assert gh.statuses == [
        {"sha": SHA, "state": "pending", "description": "x" * 140, "context": "risk-gate"}
    ]


def test_set_tier_label_replaces_other_tiers_and_creates_the_label_with_its_colour(gh):
    gh.issue_labels[5] = ["tier:T2", "module:leads"]
    gh.repo_labels["tier:T2"] = TIER_COLOURS["T2"]
    effects = Effects(gh, "shadow")
    assert effects.set_tier_label(5, "T0") == {"done": "tier:T0"}
    assert gh.issue_labels[5] == ["module:leads", "tier:T0"]
    assert gh.repo_labels["tier:T0"] == "0E8A16"
    assert effects.set_tier_label(5, "T0") == {"done": "tier:T0"}
    assert gh.issue_labels[5] == ["module:leads", "tier:T0"]


def test_set_dimension_label_and_add_and_remove(gh):
    effects = Effects(gh, "enforce")
    effects.set_dimension_label(5, "module", "leads")
    effects.set_dimension_label(5, "module", "pipeline")
    effects.add_label_if_absent(5, "triaged")
    effects.add_label_if_absent(5, "triaged")
    assert gh.issue_labels[5] == ["module:pipeline", "triaged"]
    effects.remove_label(5, "triaged")
    effects.remove_label(5, "triaged")
    assert gh.issue_labels[5] == ["module:pipeline"]


def test_a_failing_write_returns_an_error_and_the_next_write_still_runs(gh):
    gh.fail_writes = {"POST"}
    effects = Effects(gh, "enforce")
    result = effects.set_status(SHA, "pending", "waiting")
    assert result["error"].startswith("risk-gate pending: GitHub 500")
    gh.fail_writes = set()
    assert effects.upsert_comment(5, MARKER, MARKER) == {"done": "comment"}


def test_effects_has_no_dangerous_methods():
    names = {name for name in dir(Effects) if not name.startswith("__")}
    for word in ("merge", "approve", "close", "push", "edit_issue", "setting", "dismiss"):
        assert not any(word in name for name in names), word
