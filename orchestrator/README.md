# orchestrator

A separate service (Python, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, httpx, PyYAML) on port
8001. Drives the agentic build process, PR risk scoring and tiering, and the engineering signals
and forecasts that `apps/insights-web` displays.

Its database tables are prefixed `sdlc_`, with their own Alembic history (`sdlc_alembic_version`),
separate from the CRM's. Migrations live in `migrations/` (Alembic, reading `DATABASE_URL`) and
see only `sdlc_*` tables. See "Database migrations" in the root `README.md`.

Simulated engineering history (about six months of sprints, issues and pull requests, every row
`source = "synthetic"`) comes from `sdlc/synth.py`: run `python -m sdlc.synth` to add it,
`--if-empty` to skip when it already exists, or `--reset` to replace it.

Real work (issues, pull requests with their files and reviews, and CI jobs, every row
`source = "github"`) comes from `sdlc/signals/github.py`: set `GITHUB_TOKEN` and `GITHUB_REPO`
in `.env`, then run `python -m sdlc.signals.github`. Re-running updates rows in place.

The risk score is graded against history by `sdlc/calibration.py`. `python -m sdlc.risk explain
<pr>` shows one pull request's signals, score, tier and reasons (add `--source` when the number
exists in both sources); `python -m sdlc.risk calibrate` grades this database's history, and
`calibrate --generated N` grades N generated histories, pooled. Both are read-only, except that
`explain <pr> --record` appends the explained decision to the audit trail.

Every agent decision is appended to `sdlc_agent_decisions`, which is never updated or deleted
from: a correction is a new row pointing at the one it replaces. `sdlc/audit.py` records and
reads decisions, and `sdlc/agent_runs.py` parses the run records the implementer and reviewer
workflows leave in their comments (`<!-- pragmattie-run {...} -->` and
`<!-- pragmattie-review {...} -->`) and records each run once.

T3 needs two people, so a simulated second approver (`policies/approvers.yaml`, `sdlc/approver.py`)
fills the second seat. The runner asks it once for each pull request whose tier it covers, and
never approves anything itself: `python -m sdlc.approver pending` lists what is waiting, and the
repository owner runs `python -m sdlc.approver approve <pr> [--note ...]` to approve one. Every
row in `sdlc_approvals` is simulated and is never shown as a person's approval.

The triage agent (`sdlc/agents/triage.py`, on `TRIAGE_MODEL`) proposes a new issue's module,
type, priority and points, a possible duplicate and any open questions. Similar past issues come
from word overlap on titles in code (`sdlc/similarity.py`), and a duplicate is kept only if it was
one of those shown. It only proposes: it writes nothing to GitHub or the database.

`sdlc/issue_runner.py` runs it in the same poll loop and mode as the PR risk agent. It triages each
open issue once per version of its title and body (incidents and pull requests are skipped), sets
the `module:`, `type:`, `priority:` and `points:` labels, adds `needs-info` or
`possible-duplicate` when they apply, and keeps one comment explaining why
(`sdlc/agents/triage_comment.py`). A label a person has changed since the last run is left alone.
A `retriage` label runs it again and is then removed. It never closes, assigns or edits an issue.
`python -m sdlc.issue_runner once | dry-run <n> | try <n> --yes` work as the PR runner's do.

`sdlc/eval.py` grades the triage agent against a person's own labels, with bars fixed before the
first run: module 85%, type 90%, points within one step 70% (priority is reported, never gated).
`python -m sdlc.eval` grades the stored decisions on the backlog set at no cost; `--fresh` shows
what a blind re-run would send and cost, and `--fresh --yes` runs it with no labels shown and
similar issues from simulated history only, recording trial rows and writing nothing to GitHub.
`--set holdout` always runs fresh, and `--json` prints the report. The labelled sets and their
issue texts live in `eval/` and are added by a person.

The demo product backlog (four epics and 40 issues) lives in `backlog/backlog.yaml`.
`python -m sdlc.backlog` lists the labels and issues it would create in `GITHUB_REPO`; add
`--apply` to create them. Existing labels and issue titles are skipped, so it is safe to re-run.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.
