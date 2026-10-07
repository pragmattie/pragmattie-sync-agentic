import json
from datetime import datetime

import httpx
import pytest

from sdlc import github_client
from sdlc.config import Settings
from sdlc.github_client import MISSING_SETTINGS, GitHubClient, GitHubError, parse_time

TOKEN = "test-token-not-real"


def _client(handler, **kwargs):
    return GitHubClient(
        token=TOKEN, repo="acme/widgets", transport=httpx.MockTransport(handler), **kwargs
    )


@pytest.fixture
def no_settings(monkeypatch):
    for name in ("GITHUB_TOKEN", "GITHUB_REPO", "GITHUB_API_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(github_client, "get_settings", lambda: Settings(_env_file=None))


def test_missing_settings_raise_the_friendly_error(no_settings):
    with pytest.raises(GitHubError) as raised:
        GitHubClient()
    assert str(raised.value) == MISSING_SETTINGS


def test_missing_repo_alone_raises_the_friendly_error(no_settings):
    with pytest.raises(GitHubError, match="GITHUB_REPO"):
        GitHubClient(token=TOKEN)


def test_settings_repr_hides_the_token():
    settings = Settings(_env_file=None, github_token=TOKEN)
    assert TOKEN not in repr(settings)


def test_requests_carry_the_headers_and_the_repo():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"full_name": "acme/widgets"})

    assert _client(handler).get("/repos/{repo}", foo="bar") == {"full_name": "acme/widgets"}
    request = seen[0]
    assert request.url == "https://api.github.com/repos/acme/widgets?foo=bar"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert request.headers["Accept"] == "application/vnd.github+json"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"


def test_timeout_is_thirty_seconds():
    client = _client(lambda request: httpx.Response(200, json={}))
    assert client._http.timeout.read == 30


def test_base_url_can_be_overridden():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    _client(handler, base_url="https://ghe.example/api/v3").get("/repos/{repo}")
    assert seen == ["https://ghe.example/api/v3/repos/acme/widgets"]


def test_post_and_patch_send_json():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.content))
        return httpx.Response(201, json={"ok": True})

    client = _client(handler)
    assert client.post("/repos/{repo}/issues", {"title": "x"}) == {"ok": True}
    client.patch("/repos/{repo}/issues/1", {"state": "closed"})
    assert seen[0][:2] == ("POST", "/repos/acme/widgets/issues")
    assert b'"title"' in seen[0][2]
    assert seen[1][:2] == ("PATCH", "/repos/acme/widgets/issues/1")


def test_paginate_follows_next_links_across_two_pages():
    seen = []

    def handler(request):
        seen.append(request.url)
        if request.url.params.get("page") == "2":
            return httpx.Response(200, json=[{"n": 3}])
        next_url = "https://api.github.com/repos/acme/widgets/issues?state=all&per_page=100&page=2"
        return httpx.Response(
            200, json=[{"n": 1}, {"n": 2}], headers={"Link": f'<{next_url}>; rel="next"'}
        )

    items = _client(handler).paginate("/repos/{repo}/issues", state="all")
    assert [item["n"] for item in items] == [1, 2, 3]
    assert seen[0].params["per_page"] == "100"
    assert seen[0].params["state"] == "all"
    assert len(seen) == 2


def test_paginate_picks_a_list_out_of_an_object():
    def handler(request):
        return httpx.Response(200, json={"total_count": 2, "jobs": [{"id": 1}, {"id": 2}]})

    assert _client(handler).paginate("/x", key="jobs") == [{"id": 1}, {"id": 2}]


def test_a_404_raises_with_the_status_path_and_message():
    def handler(request):
        return httpx.Response(404, json={"message": "Not Found"})

    with pytest.raises(GitHubError) as raised:
        _client(handler).get("/repos/{repo}/issues/999")
    assert str(raised.value) == "GitHub 404 on /repos/acme/widgets/issues/999: Not Found"
    assert TOKEN not in str(raised.value)


def test_a_server_error_without_json_still_raises():
    def handler(request):
        return httpx.Response(502, text="Bad gateway")

    with pytest.raises(GitHubError, match="GitHub 502 on /x: Bad gateway"):
        _client(handler).get("/x")


def test_parse_time():
    assert parse_time("2026-09-01T10:15:00Z") == datetime(2026, 9, 1, 10, 15)
    assert parse_time("2026-09-01T12:15:00+02:00") == datetime(2026, 9, 1, 10, 15)
    assert parse_time(None) is None
    assert parse_time("") is None


def test_delete_and_get_text_send_the_right_method_and_accept_header():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path, request.headers["Accept"]))
        if request.method == "DELETE":
            return httpx.Response(204)
        return httpx.Response(200, text="diff --git a/x b/x\n")

    client = _client(handler)
    assert client.get_text("/repos/{repo}/pulls/3") == "diff --git a/x b/x\n"
    assert client.delete("/repos/{repo}/issues/3/labels/tier:T1") is None
    assert seen == [
        ("GET", "/repos/acme/widgets/pulls/3", "application/vnd.github.diff"),
        ("DELETE", "/repos/acme/widgets/issues/3/labels/tier:T1", "application/vnd.github+json"),
    ]


def test_an_etag_is_sent_back_and_a_304_returns_the_cached_body():
    seen = []

    def handler(request):
        seen.append(request.headers.get("If-None-Match"))
        if request.headers.get("If-None-Match") == '"v1"':
            return httpx.Response(304)
        return httpx.Response(200, json={"n": 1}, headers={"ETag": '"v1"'})

    client = _client(handler)
    assert client.get("/repos/{repo}/pulls", state="open", sort="created") == {"n": 1}
    assert client.get("/repos/{repo}/pulls", sort="created", state="open") == {"n": 1}
    assert seen == [None, '"v1"']
    assert (client.sent, client.not_modified) == (2, 1)


def test_the_etag_cache_keeps_queries_and_accept_headers_apart():
    seen = []

    def handler(request):
        seen.append(request.headers.get("If-None-Match"))
        return httpx.Response(200, json=[], headers={"ETag": '"v1"'})

    client = _client(handler)
    client.get("/x", state="open")
    client.get("/x", state="closed")
    client.get_text("/x")
    assert seen == [None, None, None]


def test_each_page_of_paginate_is_conditional():
    def handler(request):
        if request.headers.get("If-None-Match"):
            return httpx.Response(304)
        return httpx.Response(200, json=[{"n": 1}], headers={"ETag": '"p1"'})

    client = _client(handler)
    assert client.paginate("/x") == [{"n": 1}]
    assert client.paginate("/x") == [{"n": 1}]
    assert client.not_modified == 1


class Clock:
    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


NOON = datetime(2026, 10, 6, 12, 0)
RESET = int(datetime(2026, 10, 6, 12, 30, tzinfo=github_client.UTC).timestamp())


def test_a_success_with_none_left_keeps_its_body_and_later_calls_are_refused_locally():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"n": len(calls)},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(RESET)},
        )

    clock = Clock(NOON)
    client = _client(handler, now=clock)
    assert client.get("/x") == {"n": 1}
    assert client.limited_until == datetime(2026, 10, 6, 12, 30)
    with pytest.raises(github_client.RateLimited) as raised:
        client.post("/y", {})
    assert raised.value.until == datetime(2026, 10, 6, 12, 30)
    assert calls == ["/x"]
    assert client.sent == 1

    clock.moment = datetime(2026, 10, 6, 12, 30)
    assert client.get("/x") == {"n": 2}  # the reset has passed, so GitHub is asked again
    assert calls == ["/x", "/x"]


def test_a_spent_limit_raises_and_later_calls_are_refused_locally_until_the_reset():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded for installation."},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(RESET)},
        )

    clock = Clock(NOON)
    client = _client(handler, now=clock)
    with pytest.raises(github_client.RateLimited) as raised:
        client.get("/x")
    assert raised.value.until == datetime(2026, 10, 6, 12, 30)
    with pytest.raises(github_client.RateLimited):
        client.post("/y", {})
    assert calls == ["/x"]
    assert client.sent == 1

    clock.moment = datetime(2026, 10, 6, 12, 30)
    with pytest.raises(github_client.RateLimited):  # the limit is still spent at GitHub
        client.get("/x")
    assert calls == ["/x", "/x"]


def test_an_error_with_none_left_is_an_ordinary_error_that_still_sets_the_limit():
    def handler(request):
        return httpx.Response(
            404,
            json={"message": "Not Found"},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(RESET)},
        )

    client = _client(handler, now=Clock(NOON))
    with pytest.raises(GitHubError) as raised:
        client.get("/x")
    assert not isinstance(raised.value, github_client.RateLimited)
    assert client.limited_until == datetime(2026, 10, 6, 12, 30)


def test_a_403_saying_the_limit_is_spent_raises_rate_limited():
    def handler(request):
        return httpx.Response(
            403,
            json={"message": "API rate limit exceeded for installation."},
            headers={"x-ratelimit-reset": str(RESET)},
        )

    client = _client(handler, now=Clock(NOON))
    with pytest.raises(github_client.RateLimited):
        client.get("/x")
    assert client.limited_until == datetime(2026, 10, 6, 12, 30)


def test_a_secondary_limit_uses_retry_after_and_a_missing_reset_waits_a_minute():
    responses = [
        httpx.Response(429, json={"message": "slow down"}, headers={"retry-after": "120"}),
        httpx.Response(403, json={"message": "You have exceeded a secondary rate limit."}),
    ]
    client = _client(lambda request: responses.pop(0), now=Clock(NOON))
    with pytest.raises(github_client.RateLimited):
        client.get("/x")
    assert client.limited_until == datetime(2026, 10, 6, 12, 2)

    client.limited_until = None
    with pytest.raises(github_client.RateLimited):
        client.get("/x")
    assert client.limited_until == datetime(2026, 10, 6, 12, 1)


def test_a_plain_403_is_an_ordinary_error():
    def handler(request):
        return httpx.Response(403, json={"message": "Resource not accessible by integration"})

    client = _client(handler)
    with pytest.raises(GitHubError) as raised:
        client.get("/x")
    assert not isinstance(raised.value, github_client.RateLimited)
    assert client.limited_until is None


def test_graphql_posts_the_query_and_variables_and_returns_data():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"data": {"viewer": {"login": "bot"}}})

    data = _client(handler).graphql("query($n: Int!) { viewer { login } }", n=1)
    assert data == {"viewer": {"login": "bot"}}
    request = seen[0]
    assert request.method == "POST"
    assert request.url == "https://api.github.com/graphql"
    assert request.headers["Authorization"] == f"Bearer {TOKEN}"
    assert json.loads(request.content) == {
        "query": "query($n: Int!) { viewer { login } }",
        "variables": {"n": 1},
    }


def test_graphql_raises_the_first_error_message():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "data": None,
                "errors": [{"message": "Could not resolve to a ProjectV2"}, {"message": "two"}],
            },
        )

    with pytest.raises(GitHubError, match="Could not resolve to a ProjectV2") as raised:
        _client(handler).graphql("query { x }")
    assert "two" not in str(raised.value)


def test_graphql_goes_through_the_rate_limit_handling():
    calls = []

    def handler(request):
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={"data": {}},
            headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(RESET)},
        )

    client = _client(handler, now=Clock(NOON))
    client.graphql("query { x }")
    with pytest.raises(github_client.RateLimited):
        client.graphql("query { x }")
    assert calls == ["/graphql"]
