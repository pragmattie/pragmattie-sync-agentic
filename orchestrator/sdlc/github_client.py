"""A small client for GitHub's REST API.

The token comes from settings and only ever travels in the ``Authorization`` header: it is never
printed, logged or put in an error message.

Every GET is conditional: the client remembers each response's ``ETag`` per accept header, URL
and query, sends it back as ``If-None-Match``, and on a ``304`` returns the remembered body. A 304
doesn't count against GitHub's rate limit. When a response says no requests are left, the client
keeps its body and refuses every later request itself, without calling GitHub, until the limit
resets. A 403 or 429 that says the limit is spent raises ``RateLimited``. GraphQL calls go through
the same handling.
"""

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from sdlc.clock import utcnow
from sdlc.config import DEFAULT_GITHUB_API_URL, get_settings

API_VERSION = "2022-11-28"
TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100
MISSING_SETTINGS = "Set GITHUB_TOKEN and GITHUB_REPO (owner/name) in your .env file first."
JSON_ACCEPT = "application/vnd.github+json"
DIFF_ACCEPT = "application/vnd.github.diff"
DEFAULT_LIMIT_SECONDS = 60


class GitHubError(Exception):
    pass


class RateLimited(GitHubError):
    """GitHub's rate limit is spent; ``until`` is when it resets, as naive UTC."""

    def __init__(self, until: datetime):
        super().__init__(f"GitHub's rate limit is spent until {until:%Y-%m-%d %H:%M:%S} UTC.")
        self.until = until


def parse_time(text: str | None) -> datetime | None:
    """GitHub's ISO time as a naive UTC datetime, or ``None``."""
    if not text:
        return None
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment


class GitHubClient:
    def __init__(
        self,
        token: str | None = None,
        repo: str | None = None,
        base_url: str | None = None,
        transport: httpx.BaseTransport | None = None,
        now: Callable[[], datetime] = utcnow,
    ):
        settings = get_settings()
        token = token or settings.github_token
        self.repo = repo or settings.github_repo
        if not token or not self.repo:
            raise GitHubError(MISSING_SETTINGS)
        self._http = httpx.Client(
            base_url=base_url or settings.github_api_url or DEFAULT_GITHUB_API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": JSON_ACCEPT,
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )
        self._now = now
        self._etags: dict[tuple, tuple[str, httpx.Response]] = {}
        self.limited_until: datetime | None = None
        self.sent = 0
        self.not_modified = 0

    def _path(self, path: str) -> str:
        return path.replace("{repo}", self.repo)

    def rate_limited(self) -> bool:
        """Whether the client is refusing requests until ``limited_until``."""
        if self.limited_until is not None and self._now() >= self.limited_until:
            self.limited_until = None
        return self.limited_until is not None

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        if self.rate_limited():
            raise RateLimited(self.limited_until)
        response = self._http.request(method, path, **kwargs)
        self.sent += 1
        if response.headers.get("x-ratelimit-remaining") == "0" or _limit_spent(response):
            self.limited_until = self._reset_time(response)
        if _limit_spent(response):
            raise RateLimited(self.limited_until)
        if response.is_error:
            raise GitHubError(f"GitHub {response.status_code} on {path}: {_message(response)}")
        return response

    def _reset_time(self, response: httpx.Response) -> datetime:
        retry_after = response.headers.get("retry-after", "")
        if retry_after.isdigit():
            return self._now() + timedelta(seconds=int(retry_after))
        reset = response.headers.get("x-ratelimit-reset", "")
        if reset.isdigit():
            return datetime.fromtimestamp(int(reset), UTC).replace(tzinfo=None)
        return self._now() + timedelta(seconds=DEFAULT_LIMIT_SECONDS)

    def _get(
        self, url: str, params: dict[str, Any] | None = None, accept: str = JSON_ACCEPT
    ) -> httpx.Response:
        """A conditional GET: a 304 gives back the response remembered for the same request."""
        request_url = self._http.build_request("GET", url, params=params).url
        key = (
            accept,
            str(request_url.copy_with(query=None)),
            tuple(sorted(request_url.params.multi_items())),
        )
        headers = {"Accept": accept}
        cached = self._etags.get(key)
        if cached:
            headers["If-None-Match"] = cached[0]
        response = self._send("GET", url, params=params, headers=headers)
        if response.status_code == 304 and cached:
            self.not_modified += 1
            return cached[1]
        etag = response.headers.get("etag")
        if etag:
            response.read()
            self._etags[key] = (etag, response)
        return response

    def get(self, path: str, **params: Any) -> Any:
        return self._get(self._path(path), params).json()

    def get_text(self, path: str, accept: str = DIFF_ACCEPT) -> str:
        return self._get(self._path(path), accept=accept).text

    def post(self, path: str, body: dict) -> Any:
        return self._send("POST", self._path(path), json=body).json()

    def patch(self, path: str, body: dict) -> Any:
        return self._send("PATCH", self._path(path), json=body).json()

    def graphql(self, query: str, **variables: Any) -> dict:
        """Run a GraphQL query or mutation and return its ``data``."""
        body = self._send("POST", "/graphql", json={"query": query, "variables": variables}).json()
        if body.get("errors"):
            raise GitHubError(f"GitHub GraphQL: {body['errors'][0].get('message', 'error')}")
        return body.get("data") or {}

    def delete(self, path: str) -> None:
        self._send("DELETE", self._path(path))

    def paginate(self, path: str, key: str | None = None, **params: Any) -> list:
        """Every item across pages, following ``Link: rel="next"``."""
        items: list = []
        url: str | None = self._path(path)
        request_params: dict[str, Any] | None = {"per_page": PAGE_SIZE, **params}
        while url:
            response = self._get(url, request_params)
            data = response.json()
            items.extend(data[key] if key else data)
            url = response.links.get("next", {}).get("url")
            request_params = None  # the next link already carries them
        return items

    def close(self) -> None:
        self._http.close()


def _limit_spent(response: httpx.Response) -> bool:
    """Whether a 403 or 429 refused the request because the rate limit is spent."""
    if response.status_code not in (403, 429):
        return False
    if response.status_code == 429 or response.headers.get("x-ratelimit-remaining") == "0":
        return True
    return "rate limit" in _message(response).lower()


def _message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return response.reason_phrase
