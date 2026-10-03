import json

import httpx

from sdlc.backlog import (
    INTRO,
    apply,
    body_for,
    labels_for,
    load_backlog,
    main,
    plan,
    read_backlog,
)
from sdlc.github_client import GitHubClient
from sdlc.tables import MODULES

PLAIN = {
    "title": "Parent and child accounts",
    "module": "accounts",
    "type": "feature",
    "priority": "p3",
    "points": 8,
}
FULL = {
    "title": "Store daily exchange rates",
    "module": "forecasting",
    "type": "feature",
    "priority": "p1",
    "points": 5,
    "epic": "Multi-currency forecasting",
    "criteria": ["Rates are stored daily", "Missing days use the last rate"],
}


def _client(handler):
    return GitHubClient(
        token="test-token-not-real", repo="acme/widgets", transport=httpx.MockTransport(handler)
    )


def test_backlog_has_40_issues_4_epics_and_known_modules():
    backlog = read_backlog()
    items = load_backlog()
    assert len(items) == 40
    assert len(backlog["epics"]) == 4
    assert {item["module"] for item in items} <= set(MODULES)
    assert {item["epic"] for item in items if item.get("epic")} == set(backlog["epics"])
    assert len({item["title"] for item in items}) == 40


def test_labels_without_epic():
    assert labels_for(PLAIN) == ["module:accounts", "type:feature", "priority:p3", "points:8"]


def test_labels_with_epic():
    assert labels_for(FULL)[-1] == "epic:Multi-currency forecasting"
    assert len(labels_for(FULL)) == 5


def test_body_without_epic_or_criteria():
    assert body_for(PLAIN) == INTRO
    assert INTRO == "_Created from the PragMattie Sync demo backlog._"


def test_body_with_epic_and_criteria():
    assert body_for(FULL) == (
        "_Created from the PragMattie Sync demo backlog._\n\n"
        "**Epic:** Multi-currency forecasting\n\n"
        "## Acceptance criteria\n\n"
        "- [ ] Rates are stored daily\n"
        "- [ ] Missing days use the last rate"
    )


def test_body_with_criteria_only():
    item = {**PLAIN, "criteria": ["One"]}
    assert body_for(item) == f"{INTRO}\n\n## Acceptance criteria\n\n- [ ] One"


def test_plan_skips_existing_titles_and_labels():
    todo = plan([PLAIN, FULL], {"Parent and child accounts"}, {"module:accounts", "demo"})
    assert todo["issues"] == [FULL]
    assert todo["labels"] == sorted(todo["labels"])
    assert "module:accounts" not in todo["labels"]
    assert "demo" not in todo["labels"]
    assert {
        "caused-incident",
        "incident",
        "module:forecasting",
        "points:8",
        "epic:Multi-currency forecasting",
    } <= set(todo["labels"])


def test_plan_with_everything_existing_is_empty():
    todo = plan([PLAIN], {PLAIN["title"]}, set(plan([PLAIN], set(), set())["labels"]))
    assert todo == {"labels": [], "issues": []}


def test_apply_sends_labels_then_issues(capsys):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(201, json={"number": len(requests)})

    todo = {
        "labels": [
            "caused-incident",
            "demo",
            "epic:X",
            "incident",
            "module:leads",
            "points:3",
            "priority:p1",
            "type:bug",
        ],
        "issues": [FULL],
    }
    apply(_client(handler), todo)

    assert all(r.method == "POST" for r in requests)
    label_bodies = [json.loads(r.content) for r in requests[:-1]]
    assert all(r.url.path == "/repos/acme/widgets/labels" for r in requests[:-1])
    assert label_bodies == [
        {
            "name": "caused-incident",
            "color": "B60205",
            "description": "This PR caused a production incident",
        },
        {
            "name": "demo",
            "color": "D4C5F9",
            "description": "Made during a demo: the demo reset cleans it up",
        },
        {"name": "epic:X", "color": "6FBAC1"},
        {
            "name": "incident",
            "color": "D93F0B",
            "description": "A production incident: the release gate holds releases touching it",
        },
        {"name": "module:leads", "color": "1B8A94"},
        {"name": "points:3", "color": "C5CAD1"},
        {"name": "priority:p1", "color": "E8B35A"},
        {"name": "type:bug", "color": "2E3F55"},
    ]
    issue = requests[-1]
    assert issue.url.path == "/repos/acme/widgets/issues"
    assert json.loads(issue.content) == {
        "title": FULL["title"],
        "body": body_for(FULL),
        "labels": labels_for(FULL),
    }
    assert capsys.readouterr().out == f"  #9 {FULL['title']}\n"


def _github(requests, titles=(), labels=()):
    def handler(request):
        requests.append(request)
        if request.method == "GET" and request.url.path.endswith("/issues"):
            return httpx.Response(200, json=[{"title": t} for t in titles])
        if request.method == "GET" and request.url.path.endswith("/labels"):
            return httpx.Response(200, json=[{"name": n} for n in labels])
        return httpx.Response(201, json={"number": 100 + len(requests)})

    return handler


def test_cli_dry_run_makes_no_post(capsys):
    requests = []
    existing = load_backlog()[0]["title"]
    assert main([], client=_client(_github(requests, titles=[existing]))) == 0

    assert {r.method for r in requests} == {"GET"}
    issues = [r for r in requests if r.url.path.endswith("/issues")]
    assert issues[0].url.params["state"] == "all"
    out = capsys.readouterr().out
    assert out.startswith("acme/widgets: ")
    assert " labels and 39 issues to create.\n" in out
    assert existing not in out
    assert "module:leads" in out
    assert out.endswith("Dry run only. Re-run with --apply to create them.\n")


def test_cli_apply_creates_and_says_done(capsys):
    requests = []
    items = load_backlog()
    titles = [item["title"] for item in items[1:]]
    labels = sorted(set(plan(items, set(), set())["labels"]) - {"demo"})
    assert main(["--apply"], client=_client(_github(requests, titles, labels))) == 0

    posts = [r for r in requests if r.method == "POST"]
    assert [r.url.path for r in posts] == [
        "/repos/acme/widgets/labels",
        "/repos/acme/widgets/issues",
    ]
    out = capsys.readouterr().out
    assert out.startswith("acme/widgets: 1 labels and 1 issues to create.\n")
    assert f"  #{100 + len(requests)} {items[0]['title']}\n" in out
    assert out.endswith("Done.\n")
