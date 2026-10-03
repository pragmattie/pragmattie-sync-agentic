"""A small client for GitHub's REST API.

The token comes from settings and only ever travels in the ``Authorization`` header: it is never
printed, logged or put in an error message.
"""

from datetime import UTC, datetime
from typing import Any

import httpx

from sdlc.config import DEFAULT_GITHUB_API_URL, get_settings

API_VERSION = "2022-11-28"
TIMEOUT_SECONDS = 30.0
PAGE_SIZE = 100
MISSING_SETTINGS = "Set GITHUB_TOKEN and GITHUB_REPO (owner/name) in your .env file first."


class GitHubError(Exception):
    pass


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
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
            },
            timeout=TIMEOUT_SECONDS,
            transport=transport,
        )

    def _path(self, path: str) -> str:
        return path.replace("{repo}", self.repo)

    def _send(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = self._http.request(method, path, **kwargs)
        if response.is_error:
            raise GitHubError(f"GitHub {response.status_code} on {path}: {_message(response)}")
        return response

    def get(self, path: str, **params: Any) -> Any:
        return self._send("GET", self._path(path), params=params).json()

    def post(self, path: str, body: dict) -> Any:
        return self._send("POST", self._path(path), json=body).json()

    def patch(self, path: str, body: dict) -> Any:
        return self._send("PATCH", self._path(path), json=body).json()

    def paginate(self, path: str, key: str | None = None, **params: Any) -> list:
        """Every item across pages, following ``Link: rel="next"``."""
        items: list = []
        url: str | None = self._path(path)
        request_params: dict[str, Any] | None = {"per_page": PAGE_SIZE, **params}
        while url:
            response = self._send("GET", url, params=request_params)
            data = response.json()
            items.extend(data[key] if key else data)
            url = response.links.get("next", {}).get("url")
            request_params = None  # the next link already carries them
        return items

    def close(self) -> None:
        self._http.close()


def _message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text[:200] or response.reason_phrase
    if isinstance(data, dict) and data.get("message"):
        return str(data["message"])
    return response.reason_phrase
