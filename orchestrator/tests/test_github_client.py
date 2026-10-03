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
