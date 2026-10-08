"""In-memory stand-ins for GitHub and Claude, so the poll loop runs without either.

``FakeGitHub`` has the client's interface (``get``, ``get_text``, ``post``, ``patch``, ``delete``,
``paginate``) over in-memory pull requests, issues, comments, reviews, statuses and labels, and
logs every request it is sent. ``FakeLLM`` answers with a fixed adjustment or raises a given error.
"""

import re
from datetime import datetime
from urllib.parse import unquote

from sdlc.agents.llm import LLMResult, StructuredLLM
from sdlc.github_client import GitHubError

REPO = "acme/widgets"
BOT = {"login": "pragmattie-orchestrator[bot]", "type": "Bot"}
WORKFLOW_BOT = {"login": "github-actions[bot]", "type": "Bot"}
PERSON = {"login": "pat", "type": "User"}


def iso(moment: datetime) -> str:
    return moment.isoformat() + "Z"


class FakeGitHub:
    def __init__(self):
        self.repo = REPO
        self.prs: dict[int, dict] = {}
        self.issues: dict[int, dict] = {}
        self.files: dict[int, list[dict]] = {}
        self.reviews: dict[int, list[dict]] = {}
        self.comments: list[dict] = []  # each carries its issue "number"
        self.issue_labels: dict[int, list[str]] = {}
        self.issue_events: dict[int, list[dict]] = {}
        self.repo_labels: dict[str, str] = {}
        self.statuses: list[dict] = []
        self.diffs: dict[int, str] = {}
        self.requests: list[tuple[str, str]] = []
        self.fail_writes: set[str] = set()  # methods that raise, e.g. {"POST"}
        self.limited_until = None
        self.sent = 0
        self.not_modified = 0
        self._next_id = 1000

    # Test helpers

    def add_pr(
        self,
        number: int,
        sha: str,
        *,
        files=("apps/crm-web/src/App.vue",),
        draft=False,
        body="Adds a field.",
        additions=10,
        created_at=datetime(2026, 10, 1, 10, 0),
    ) -> dict:
        self.prs[number] = {
            "number": number,
            "title": f"Change {number}",
            "body": body,
            "state": "open",
            "draft": draft,
            "head": {"sha": sha},
            "labels": [],
            "user": {"login": "ada-dev", "type": "User"},
            "created_at": iso(created_at),
            "merged_at": None,
            "closed_at": None,
            "merge_commit_sha": None,
            "changed_files": len(files),
            "additions": additions,
            "deletions": 2,
            "commits": 1,
        }
        self.files[number] = [{"filename": name} for name in files]
        self.reviews.setdefault(number, [])
        self.diffs[number] = "".join(f"diff --git a/{name} b/{name}\n+change\n" for name in files)
        return self.prs[number]

    def add_issue(
        self,
        number: int,
        title: str = "Import leads from a CSV file",
        body: str | None = "Sales reps want to upload a spreadsheet of leads.",
        *,
        labels=(),
        created_at=datetime(2026, 10, 1, 9, 0),
    ) -> dict:
        self.issues[number] = {
            "number": number,
            "title": title,
            "body": body,
            "state": "open",
            "user": dict(PERSON),
            "assignee": None,
            "created_at": iso(created_at),
            "closed_at": None,
        }
        self.issue_labels[number] = list(labels)
        return self.issues[number]

    def label(self, number: int, name: str, at: datetime) -> None:
        """A person adds a label, leaving a ``labeled`` event like GitHub's."""
        self.issue_labels[number].append(name)
        event = {"event": "labeled", "label": {"name": name}, "created_at": iso(at)}
        self.issue_events.setdefault(number, []).append(event)

    def push(self, number: int, sha: str) -> None:
        self.prs[number]["head"]["sha"] = sha

    def merge(self, number: int, at: datetime) -> None:
        self.prs[number].update(state="closed", merged_at=iso(at), closed_at=iso(at))

    def comment(self, number: int, body: str, *, user=PERSON, at=None) -> dict:
        self._next_id += 1
        comment = {
            "id": self._next_id,
            "number": number,
            "body": body,
            "user": dict(user),
            "created_at": iso(at or datetime(2026, 10, 1, 10, 0)),
            "html_url": f"https://github.example/{REPO}/pull/{number}#c{self._next_id}",
        }
        self.comments.append(comment)
        return comment

    def review(self, number: int, sha: str, state="APPROVED", user=PERSON) -> None:
        self.reviews[number].append(
            {"user": dict(user), "state": state, "commit_id": sha, "submitted_at": None}
        )

    def risk_comment(self, number: int) -> dict | None:
        return next(
            (c for c in self.comments if c["number"] == number and c["user"]["type"] == "Bot"),
            None,
        )

    def statuses_for(self, sha: str) -> list[dict]:
        return [status for status in self.statuses if status["sha"] == sha]

    def writes(self) -> list[tuple[str, str]]:
        return [request for request in self.requests if request[0] != "GET"]

    # The client's interface

    def rate_limited(self) -> bool:
        return self.limited_until is not None

    def _log(self, method: str, path: str) -> str:
        path = path.replace("{repo}", REPO).removeprefix(f"/repos/{REPO}")
        self.requests.append((method, path))
        self.sent += 1
        if method in self.fail_writes:
            raise GitHubError(f"GitHub 500 on {path}: fake failure")
        return path

    def get(self, path: str, **params):
        path = self._log("GET", path)
        if match := re.fullmatch(r"/pulls/(\d+)", path):
            return self._pr(int(match.group(1)))
        if match := re.fullmatch(r"/issues/(\d+)", path):
            number = int(match.group(1))
            return self._issue(number) if number in self.issues else self._pr(number)
        raise GitHubError(f"GitHub 404 on {path}: Not Found")

    def get_text(self, path: str, accept: str = "application/vnd.github.diff") -> str:
        path = self._log("GET", path)
        number = int(re.fullmatch(r"/pulls/(\d+)", path).group(1))
        return self.diffs[number]

    def paginate(self, path: str, key=None, **params) -> list:
        path = self._log("GET", path)
        if path == "/pulls":
            state = params.get("state", "open")
            return [
                self._pr(number)
                for number, pr in self.prs.items()
                if state == "all" or pr["state"] == state
            ]
        if path == "/issues":  # GitHub lists pull requests as issues too
            state = params.get("state", "open")
            prs = [{**self._pr(n), "pull_request": {}} for n in self.prs]
            issues = [self._issue(number) for number in self.issues]
            return [item for item in issues + prs if state == "all" or item["state"] == state]
        if path == "/labels":
            return [{"name": name, "color": colour} for name, colour in self.repo_labels.items()]
        if path == "/issues/comments":
            since = params.get("since")
            return [c for c in self.comments if since is None or c["created_at"] >= since]
        if match := re.fullmatch(r"/pulls/(\d+)/(files|reviews)", path):
            number = int(match.group(1))
            return list((self.files if match.group(2) == "files" else self.reviews)[number])
        if match := re.fullmatch(r"/issues/(\d+)/comments", path):
            return [c for c in self.comments if c["number"] == int(match.group(1))]
        if match := re.fullmatch(r"/issues/(\d+)/labels", path):
            return [{"name": name} for name in self.issue_labels.get(int(match.group(1)), [])]
        if match := re.fullmatch(r"/issues/(\d+)/events", path):
            return list(self.issue_events.get(int(match.group(1)), []))
        raise GitHubError(f"GitHub 404 on {path}: Not Found")

    def post(self, path: str, body: dict):
        path = self._log("POST", path)
        if match := re.fullmatch(r"/issues/(\d+)/comments", path):
            return self.comment(int(match.group(1)), body["body"], user=BOT)
        if match := re.fullmatch(r"/statuses/(\w+)", path):
            self.statuses.append({"sha": match.group(1), **body})
            return body
        if path == "/labels":
            self.repo_labels[body["name"]] = body["color"]
            return body
        if match := re.fullmatch(r"/issues/(\d+)/labels", path):
            labels = self.issue_labels.setdefault(int(match.group(1)), [])
            labels += [name for name in body["labels"] if name not in labels]
            return labels
        raise GitHubError(f"GitHub 404 on {path}: Not Found")

    def patch(self, path: str, body: dict):
        path = self._log("PATCH", path)
        comment_id = int(re.fullmatch(r"/issues/comments/(\d+)", path).group(1))
        comment = next(c for c in self.comments if c["id"] == comment_id)
        comment["body"] = body["body"]
        return comment

    def delete(self, path: str) -> None:
        path = self._log("DELETE", path)
        match = re.fullmatch(r"/issues/(\d+)/labels/(.+)", path)
        self.issue_labels[int(match.group(1))].remove(unquote(match.group(2)))

    def _issue(self, number: int) -> dict:
        labels = [{"name": name} for name in self.issue_labels.get(number, [])]
        return {**self.issues[number], "labels": labels}

    def _pr(self, number: int) -> dict:
        if number not in self.prs:
            raise GitHubError(f"GitHub 404 on /pulls/{number}: Not Found")
        labels = [{"name": name} for name in self.issue_labels.get(number, [])]
        return {**self.prs[number], "labels": labels}


def answer(adjustment: int = 0) -> dict:
    return {
        "adjustment": adjustment,
        "justification": "The rubric tells the story.",
        "top_reasons": ["one", "two", "three"],
        "test_gaps": [],
    }


class FakeLLM:
    """Stands in for StructuredLLM: logs each call, then answers or raises ``error``."""

    def __init__(self, data=None, error=None, model="claude-sonnet-5", max_tokens=1500):
        self.data = data if data is not None else answer()
        self.error = error
        self.model = model
        self.max_tokens = max_tokens
        self.calls: list[dict] = []

    def call(self, **kwargs) -> LLMResult:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return LLMResult(
            data=self.data,
            model=self.model,
            input_tokens=1200,
            output_tokens=150,
            cache_read_tokens=0,
            cache_write_tokens=0,
            latency_ms=900,
            request_id="req_1",
            cost_usd=0.0039,
        )

    def request_kwargs(self, system, user, schema, effort):
        return StructuredLLM(
            client=object(), model=self.model, max_tokens=self.max_tokens
        ).request_kwargs(system, user, schema, effort)
