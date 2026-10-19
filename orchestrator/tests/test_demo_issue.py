from datetime import datetime

from sdlc import demo_issue
from tests.fakes import REPO, FakeGitHub

CONTACT_TITLE = "Demo: Account page: the Contacts card shows how many contacts there are"
FILTERS_TITLE = 'Demo: Accounts: a "Clear filters" button when no accounts match'


def _repository(*, milestone: bool = True) -> FakeGitHub:
    gh = FakeGitHub()
    gh.add_issue(1, "8.6 One command opens the live demo's issue")
    gh.milestones = [{"number": 7, "title": "M7 Cutover", "state": "open"}]
    if milestone:
        gh.milestones.append({"number": 8, "title": "M8 Polish", "state": "open"})
    return gh


def test_the_four_demo_changes_are_there_and_well_formed():
    assert demo_issue.names() == [
        "account-contact-count",
        "account-opportunity-count",
        "accounts-clear-filters",
        "contact-phone-link",
    ]
    change = demo_issue.load("account-contact-count")
    assert change.issue_title == CONTACT_TITLE
    assert change.body.startswith("## Goal\n")
    assert "## Part\n\nPragMattie Sync CRM\n" in change.body


def test_list_shows_all_four_changes_fresh(capsys):
    gh = _repository()
    assert demo_issue.main(["list"], gh=gh) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 4
    assert lines[0].startswith("account-contact-count")
    assert "fresh" in lines[0] and CONTACT_TITLE.removeprefix("Demo: ") in lines[0]
    assert lines[1].startswith("account-opportunity-count") and "fresh" in lines[1]
    assert lines[2].startswith("accounts-clear-filters") and "fresh" in lines[2]
    assert lines[3].startswith("contact-phone-link") and "fresh" in lines[3]
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
    assert all("fresh" in line for line in lines[1:])


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
        "accounts-clear-filters, contact-phone-link."
    )


def test_a_used_change_is_refused(capsys):
    gh = _repository()
    gh.add_issue(240, CONTACT_TITLE, labels=["demo"])
    assert demo_issue.main(["open", "account-contact-count"], gh=gh) == 1
    assert gh.writes() == []
    err = capsys.readouterr().err.strip()
    assert "already used: #240 (open)" in err and "`list`" in err


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
