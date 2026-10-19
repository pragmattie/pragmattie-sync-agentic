import re
from datetime import datetime

from sdlc import demo_issue
from tests.fakes import REPO, FakeGitHub

CONTACT_TITLE = "Demo: Account page: the Contacts card shows how many contacts there are"
FILTERS_TITLE = 'Demo: Accounts: a "Clear filters" button when no accounts match'
REHEARSAL_TITLE = "Demo: About page: show build"


def _repository(*, milestone: bool = True) -> FakeGitHub:
    gh = FakeGitHub()
    gh.add_issue(1, "8.6 One command opens the live demo's issue")
    gh.milestones = [{"number": 7, "title": "M7 Cutover", "state": "open"}]
    if milestone:
        gh.milestones.append({"number": 8, "title": "M8 Polish", "state": "open"})
    return gh


def test_the_five_demo_changes_are_there_and_well_formed():
    assert demo_issue.names() == [
        "account-contact-count",
        "account-opportunity-count",
        "accounts-clear-filters",
        "contact-phone-link",
        "rehearsal-about-note",
    ]
    change = demo_issue.load("account-contact-count")
    assert change.issue_title == CONTACT_TITLE
    assert change.body.startswith("## Goal\n")
    assert "## Part\n\nPragMattie Sync CRM\n" in change.body


def test_list_shows_the_four_one_use_changes_fresh(capsys):
    gh = _repository()
    assert demo_issue.main(["list"], gh=gh) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 5
    assert lines[0].startswith("account-contact-count")
    assert "fresh" in lines[0] and CONTACT_TITLE.removeprefix("Demo: ") in lines[0]
    assert lines[1].startswith("account-opportunity-count") and "fresh" in lines[1]
    assert lines[2].startswith("accounts-clear-filters") and "fresh" in lines[2]
    assert lines[3].startswith("contact-phone-link") and "fresh" in lines[3]
    assert lines[4].startswith("rehearsal-about-note") and "repeatable (next: 1)" in lines[4]
    assert gh.writes() == []


def test_list_shows_a_closed_demo_issue_as_used(capsys):
    gh = _repository()
    gh.add_issue(240, CONTACT_TITLE, labels=["demo"])
    gh.close_issue(240, datetime(2026, 10, 18, 12, 0))
    gh.add_pr(241, "sha241")
    gh.prs[241]["title"] = FILTERS_TITLE  # a pull request doesn't use a change
    assert demo_issue.main(["list"], gh=gh) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "used: #240 (closed)" in lines[0]
    assert all("fresh" in line for line in lines[1:4])


def test_open_creates_the_issue_in_one_request(capsys):
    gh = _repository()
    assert demo_issue.main(["open", "account-contact-count"], gh=gh) == 0
    assert gh.writes() == [("POST", "/issues")]
    issue = gh.issues[2]
    assert issue["title"] == CONTACT_TITLE
    assert issue["body"] == demo_issue.load("account-contact-count").body
    assert issue["milestone"] == 8
    assert gh.issue_labels[2] == ["demo", "forecast:T1", "agent-ready"]
    out = capsys.readouterr().out
    assert f"Title:     {CONTACT_TITLE}" in out
    assert "Milestone: M8 Polish" in out
    assert "Labels:    demo, forecast:T1, agent-ready" in out
    assert f"Created #2: https://github.com/{REPO}/issues/2" in out
    assert "Triage labels it within a minute." in out
    assert "The builder starts now" in out


def test_the_dry_run_prints_the_issue_and_creates_nothing(capsys):
    gh = _repository()
    assert demo_issue.main(["open", "accounts-clear-filters", "--dry-run"], gh=gh) == 0
    assert gh.writes() == []
    out = capsys.readouterr().out
    assert FILTERS_TITLE in out and "M8 Polish" in out
    assert "## Acceptance criteria" in out
    assert "Created" not in out


def test_an_unknown_name_is_refused(capsys):
    gh = _repository()
    assert demo_issue.main(["open", "dark-mode"], gh=gh) == 1
    assert gh.writes() == []
    err = capsys.readouterr().err.strip()
    assert err == (
        "No demo change named 'dark-mode'. "
        "There are: account-contact-count, account-opportunity-count, "
        "accounts-clear-filters, contact-phone-link, rehearsal-about-note."
    )


def test_a_used_change_is_refused(capsys):
    gh = _repository()
    gh.add_issue(240, CONTACT_TITLE, labels=["demo"])
    assert demo_issue.main(["open", "account-contact-count"], gh=gh) == 1
    assert gh.writes() == []
    err = capsys.readouterr().err.strip()
    assert err == (
        "account-contact-count is already used: #240 (open). "
        "Run ./scripts/demo.ps1 issue to see the fresh ones."
    )


def test_no_open_m8_milestone_is_refused(capsys):
    gh = _repository(milestone=False)
    gh.milestones.append({"number": 8, "title": "M8 Polish", "state": "closed"})
    assert demo_issue.main(["open", "account-contact-count"], gh=gh) == 1
    assert gh.writes() == []
    assert "No open milestone" in capsys.readouterr().err


def test_a_malformed_file_is_refused(tmp_path, capsys):
    (tmp_path / "broken.md").write_text("Contacts count\n\n## Goal\n")
    gh = _repository()
    assert demo_issue.main(["open", "broken"], gh=gh, folder=tmp_path) == 1
    assert gh.writes() == []
    assert capsys.readouterr().err.strip() == "broken.md: the first line must be '# <title>'."


def _rehearsed(gh: FakeGitHub) -> FakeGitHub:
    gh.add_issue(250, f"{REHEARSAL_TITLE} 2", labels=["demo"])
    gh.close_issue(250, datetime(2026, 10, 19, 12, 0))
    gh.add_issue(245, f"{REHEARSAL_TITLE} 1", labels=["demo"])
    gh.close_issue(245, datetime(2026, 10, 19, 10, 0))
    gh.add_issue(246, f"{REHEARSAL_TITLE} 2b")  # not a whole number
    gh.add_pr(251, "sha251")
    gh.prs[251]["title"] = f"{REHEARSAL_TITLE} 7"  # a pull request doesn't count
    return gh


def test_the_first_rehearsal_is_numbered_1(capsys):
    gh = _repository()
    assert demo_issue.main(["open", "rehearsal-about-note"], gh=gh) == 0
    assert gh.writes() == [("POST", "/issues")]
    issue = gh.issues[2]
    assert issue["title"] == f"{REHEARSAL_TITLE} 1"
    assert "This is build 1." in issue["body"]
    assert '"Build 1"' in issue["body"]
    assert "{n}" not in issue["title"] + issue["body"]
    assert gh.issue_labels[2] == ["demo", "forecast:T1", "agent-ready"]
    assert f"Title:     {REHEARSAL_TITLE} 1" in capsys.readouterr().out


def test_the_next_rehearsal_follows_the_highest_issue_number():
    gh = _rehearsed(_repository())
    change = demo_issue.load("rehearsal-about-note")
    assert change.repeatable
    assert demo_issue.next_number(gh, change) == 3


def test_list_shows_a_rehearsal_as_repeatable_with_its_next_number(capsys):
    gh = _rehearsed(_repository())
    gh.add_issue(240, CONTACT_TITLE, labels=["demo"])
    assert demo_issue.main(["list"], gh=gh) == 0
    lines = capsys.readouterr().out.splitlines()
    assert "used: #240 (open)" in lines[0]
    assert all("fresh" in line for line in lines[1:4])
    assert lines[4].startswith("rehearsal-about-note")
    assert "repeatable (next: 3)" in lines[4]
    assert "About page: show build {n}" in lines[4]
    assert gh.writes() == []


def test_a_rehearsal_is_never_refused_as_used(capsys):
    gh = _rehearsed(_repository())
    assert demo_issue.main(["open", "rehearsal-about-note"], gh=gh) == 0
    created = max(gh.issues)
    assert gh.issues[created]["title"] == f"{REHEARSAL_TITLE} 3"
    assert demo_issue.main(["open", "rehearsal-about-note"], gh=gh) == 0
    assert gh.issues[max(gh.issues)]["title"] == f"{REHEARSAL_TITLE} 4"
    assert "already used" not in capsys.readouterr().err


def test_the_rehearsal_dry_run_prints_the_numbered_issue_and_creates_nothing(capsys):
    gh = _rehearsed(_repository())
    assert demo_issue.main(["open", "rehearsal-about-note", "--dry-run"], gh=gh) == 0
    assert gh.writes() == []
    out = capsys.readouterr().out
    assert f"Title:     {REHEARSAL_TITLE} 3" in out
    assert "This is build 3." in out
    assert "{n}" not in out
    assert "Dry run: nothing was created." in out


def test_the_rehearsal_body_has_no_word_the_crm_build_check_forbids():
    body = demo_issue.load("rehearsal-about-note").numbered(1).body
    for word in ("demo", "simulated", "fictional"):
        assert not re.search(rf"\b{word}\b", body, re.IGNORECASE), word
