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

Real work (milestones as epics, issues, pull requests with their files and reviews, and CI jobs,
every row `source = "github"`) comes from `sdlc/signals/github.py`: set `GITHUB_TOKEN` and
`GITHUB_REPO` in `.env`, then run `python -m sdlc.signals.github` (or `once`) to collect once, or
`run --every SECONDS` to collect on a schedule. Re-running updates rows in place. An issue's epic
is its milestone's title, or its `epic:` label when it has no milestone; `sdlc.epics.epic_label`
shows "M5 Delivery forecasting" as "Delivery forecasting (M5)". The collector only sends GETs.
CI jobs come only from the CI workflow (`ci.yml`); with `run`, a finished run's jobs are read once
per process, and a collection that stores CI jobs deletes the rows of jobs that aren't CI's.

The risk score is graded against history by `sdlc/calibration.py`. `python -m sdlc.risk explain
<pr>` shows one pull request's signals, score, tier and reasons (add `--source` when the number
exists in both sources); `python -m sdlc.risk calibrate` grades this database's history, and
`calibrate --generated N` grades N generated histories, pooled. Both are read-only, except that
`explain <pr> --record` appends the explained decision to the audit trail.

The delivery forecast in `sdlc/forecast.py` is a Monte Carlo simulation: it resamples the issues
closed on each working day of the last 84 days and plays the open work forward 10,000 times for
P50, P85 and the chance of finishing on time, seeded by the sprint and the day so the same day
repeats. Each run draws from its own sequence (from the seed and the run's number), so fewer
remaining items never finish later; with nothing remaining the chance is 100%. `python -m sdlc.forecast [--runs N]` prints the current simulated sprint's forecast,
with the items at risk; it is read-only.

How often the predictions matched what happened is reported by `sdlc/accuracy.py`, at
`GET /api/v1/signals/accuracy` and by `python -m sdlc.accuracy` (JSON). Two trends are simulated:
each past synthetic sprint forecast again as of its third working day, from only what was known
then (sampling from the history's first closure on), and the risk score's catch of incident PRs
(T2 or above) by sprint; both are kept in the process until the day or the simulated history
changes. Three are real: the triage agent's labels kept or corrected by a person, the test
selector's settled results that missed a failure, and each finished milestone's first saved
forecast against when its last issue closed. No Claude or GitHub call is made.

Real milestones are forecast in sequence, the way the agents build them: a milestone's forecast
plays forward the open items of the open milestones before it (by the number after M) and then
its own, and every real milestone shares one seed per day, so a later milestone is never forecast
earlier than one before it. The saved `ahead_items` is that count, and the planner measures a
deferral against the same queue. Each gate still ahead (an open milestone before it, or, with none,
its own specs while it has no pull request) moves every run's finish later by the gap measured
between past milestones: the median working days from one milestone's last merge to the next's
first pull request, or 1 while fewer than 3 gaps are known. The saved `gate_days` is that total,
and the planner's deferral effect includes it. Simulated epics are each forecast alone, at their
own pace, with no gates.

The forecaster agent in `sdlc/forecaster.py` saves forecasts to `sdlc_forecasts` (append-only), so
a page can show how a date moved. It fingerprints what the forecast depends on (the day, and each
open item's id, points, module and first pull request) and saves a fresh forecast only when that
changes: `schedule` for a subject's first forecast of the day, `change` for one later that day,
`manual` when forced. Each save is one forecast row and one audit row; it makes no model call and
no GitHub request. It runs after the PR risk and triage agents in `python -m sdlc.runner run`
(`ORCHESTRATOR_MODE=off` stops it there) and after every collection in the collector, whatever
the mode. `python -m sdlc.forecaster once` polls once and `now` saves a fresh forecast of
everything; both follow `ORCHESTRATOR_MODE`. `python -m sdlc.synth --reset` forgets the simulated
forecasts and the forecaster's audit rows about them, and keeps real forecasts and every other
agent's rows.

The planner runner in `sdlc/plan_runner.py` asks the planner agent (`sdlc/agents/planner.py`) for
options once per slipping forecast of an open milestone, and records one audit row per call. A
failed call is retried up to three times, five minutes apart, and a milestone gets at most four
calls a day (failed ones included, trial calls aside). It writes nothing else and never touches
GitHub. It is the one part that spends money, so `PLANNER_MODE` (`off` by default) is its own
switch: when `on`, it runs after the forecaster in the collector, and in `python -m sdlc.runner
run` when `ORCHESTRATOR_MODE` isn't `off` either. `python -m sdlc.plan_runner dry-run
[--milestone N]` prints the exact request and a cost ceiling without calling anything, `try --yes
[--milestone N]` makes one real call and records only a trial row, and `once` polls once; `N` is
the M-number in the milestone's title (`5` or `M5`) or the full title. The forecast page's
`proposals` show each milestone's latest proposal, and `synth --reset` keeps every planner row.

The release runner in `sdlc/release_runner.py` gates real releases. The deploy workflow
(`.github/workflows/deploy.yml`) runs on every push to `main` and waits for a
`release-gate` commit status; each poll, the runner finds the deploy runs still waiting, works out
what each would ship (everything merged since the last deploy, through the PR whose merge commit
it is) and judges it with the release gate's rules (`sdlc/agents/release_gate.py`): T0 and T1
deploy at once, T2 waits for CI and for any open incident in its modules to close, and T3 then
waits for a person to approve the deployment in GitHub. It posts the verdict as the status and
records one `release_gate` audit row each time the verdict changes. It runs last in `python -m
sdlc.runner run`, in the same mode, and `python -m sdlc.release_runner once` polls once. Until
cutover (7.5) v1 posts the status on this repository, and v2's poll loop runs here in observe.

**Modes.** `ORCHESTRATOR_MODE` switches every agent in the poll loop (`python -m sdlc.runner run`,
the `agents` Compose service: `docker compose --profile agents up -d`). `off` does nothing, not
even a read. `observe` polls and decides exactly as shadow and records the same audit rows, but
writes nothing to GitHub (`sdlc/agents/github_effects.py` returns `{"skipped": "mode is
observe"}` without sending a request), so v2 can watch this repository beside v1 with a read-only
token. The risk comment it would post is kept in the risk row's `action_taken`, and
`sdlc_gate_status` and the release verdicts say "Observe (would be …)". The status and tier label
it would set are noted in the log once a commit; a `retriage` label counts once per issue
version; labels differing from the triage agent's last answer are not a person's correction.
`shadow` writes everything, but the statuses always pass and say what they would be. `enforce`
makes the gates real.

Every agent decision is appended to `sdlc_agent_decisions`, which is never updated or deleted
from: a correction is a new row pointing at the one it replaces. `sdlc/audit.py` records and
reads decisions, and `sdlc/agent_runs.py` parses the run records the implementer and reviewer
workflows leave in their comments (`<!-- pragmattie-run {...} -->` and
`<!-- pragmattie-review {...} -->`) and records each run once. The decision log reads them back
at `GET /api/v1/signals/decisions` (newest first, filterable by agent, subject, status and tier,
paginated, with run, token and cost totals over the whole filtered set, trial rows included),
`/decisions/agents` and `/decisions/{id}`.

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
Both sets are v1's issues, not this repository's (the backlog is v1's #6-#45 renumbered
8006-8045, the holdout 9001-9020), so there are no stored decisions to grade and both always run
fresh. `python -m sdlc.eval [--set backlog|holdout]` shows what a blind re-run would send and
cost; `--yes` runs it with no labels shown and similar issues from simulated history only,
recording trial rows as simulated and writing nothing to GitHub, then grades those answers.
`--fresh` is accepted and changes nothing, and `--json` prints the report. The labelled sets and
their issue texts live in `eval/` and are added by a person.

The demo product backlog (four epics and 40 issues) lives in `backlog/backlog.yaml`.
`python -m sdlc.backlog` lists the labels and issues it would create in `GITHUB_REPO`; add
`--apply` to create them. Existing labels and issue titles are skipped, so it is safe to re-run.

The delivery board is the existing GitHub Projects board "PragMattie Sync delivery"
(`BOARD_OWNER`, default `pragmattie`, and `BOARD_NUMBER`, default `1`); `sdlc/board.py` adopts it
and never creates a new one. `python -m sdlc.board setup` checks it has the Status options Backlog,
Triaged, In progress, In review, Gated, Merged and Production, in that order, and the fields
Points (number), Module (single select, one option per module), Forecast tier and Tier (single
select, T0-T3) and Risk (number), and changes nothing. `setup --apply` also creates the missing
fields, with their options. It never deletes or renames a field or an option, deletes an item, or
replaces an existing field's options (GitHub gives replaced options new ids, which would clear
every card's status): a missing or misordered Status option, a missing option or a field of the
wrong type is reported with where to fix it in the project's settings, `setup` exits 1, and
`--apply` creates nothing until it is fixed. The token needs **Organization → Projects: read and
write**.

Views can't be set through GitHub's API, so set this one by hand: a view named "Delivery", Board
layout, columns by Status, swimlanes by Milestone, with the fields Points, Module, Forecast tier,
Tier and Risk shown. `setup` prints the same layout as a reminder.

**The board is computed, not edited by people.** `python -m sdlc.board sync` recomputes every
issue's card from GitHub alone and sets each value that differs, so a card dragged by hand is moved
back on the next run. To move work, change what is true on GitHub: labels, pull requests,
approvals, `/hold`. It reads the board's items and the open issues plus issues closed in the last
14 days, with their labels, comments and the pull requests that close them (each one's head
`risk-gate` status, `tier:` label and risk comment), all through GraphQL. The most advanced
matching column wins:

- an issue closed as not planned (or as a duplicate) is left alone;
- **Merged**: the issue is closed as completed, or a pull request that closes it has merged
  (Production arrives with the release runner in M6);
- **Gated**: an open pull request whose `risk-gate` is pending or failing;
- **In review**: an open, ready pull request whose `risk-gate` passed;
- **In progress**: a draft pull request, an open one not scored yet, or an issue labelled
  `agent-ready` with no open pull request, unless it is labelled `plan-proposed` and no person
  (not a bot) has commented `/approve-plan` since the latest `## Plan` comment: that waits in
  **Triaged** until the person acts;
- **Backlog**: labelled `spec-draft` or `needs-info`, or missing a `module:` or `points:` label;
- **Triaged** otherwise.

Points, Module and Forecast tier come from the issue's `points:`, `module:` and `forecast:` labels.
Tier and Risk come from the deciding (most advanced) pull request: its `tier:` label and the score
in its risk comment (the one marked `<!-- pragmattie-risk-gate -->`); either is cleared when there
is none. It adds open issues that aren't on the board, and never removes or archives an item,
touches a pull-request item or edits an issue. `sync --dry-run` prints every change and sends
nothing. It writes no audit rows and needs only `GITHUB_TOKEN` and `GITHUB_REPO`, not the database.
It runs every 10 minutes as a GitHub Actions workflow (`.github/workflows/board.yml`);
`ci/proposed/board.yml` proposes running it on issue, pull-request, status and agent-run events
too.

**Demo reset.** Between demos, `sdlc/demo_reset.py` puts the demo back to a clean start. Only
items marked as demo are touched: issues labelled `demo` (or `demo:<x>`), and pull requests
labelled so or opened from a branch starting `demo-`. Real work is never closed or deleted. When
the repository is in a clean state, record what it holds with `python -m sdlc.demo_reset baseline`
(`--force` replaces an existing one); it is saved to `orchestrator/.demo/baseline.json`, which is
git-ignored, and a baseline for another repository is refused. `python -m sdlc.demo_reset` is a
dry run: it prints each GitHub request it would send and changes nothing. `--apply` sends them in
order (a failed one doesn't stop the rest): demo issues opened since the baseline lose their
`incident` label and are closed as not planned; backlog issues get back their state and labels;
demo pull requests are closed, lose their `tier:` labels and the agents' comments (people's stay)
and their branch, when it is in this repository and isn't the default or a baseline branch;
deployments of a merged demo PR's commit are marked inactive and deleted. Then the database forgets
the audit rows, simulated approvals, gate states and incidents about the demo items, regenerates
the simulated history for today (real forecasts and planner rows are kept), collects from GitHub
and saves fresh forecasts. Merged commits, closed issues (GitHub can't delete issues), release-gate
statuses, deploy logs and branches with no pull request stay, and are listed.

The reset needs a token with **Issues, Pull requests, Contents (to delete branches) and Deployments:
read and write**. A read-only token can run the dry run; with it, `--apply` lists every GitHub
action as failed (the database part still runs). From the repository root, `scripts/demo.ps1`
wraps it for the Docker stack: `reset` (the dry run), `reset -Apply` (pauses the `collector` if it
is running, reseeds the CRM with `python -m app.seed --reset`, runs the reset, then restarts the
collector, even after a failure; then it runs the check), `baseline [-Force]`, and
`check [-Smoke]`.

**Pre-demo check.** `python -m sdlc.demo_check` says whether everything is ready for a demo, one
line per check with the fix for anything that isn't: the database (if it's down, nothing else is
checked), both Alembic histories at head, at least 20 accounts, 50 leads and 50 opportunities in
the CRM, a simulated sprint in progress today and at least 300 simulated PRs, forecasts saved
today, both APIs' health, GitHub access (if it fails, the GitHub checks are skipped), a recorded
demo baseline, the release setup (the `demo` and `incident` labels, `deploy.yml` on the default
branch, the policy's two environments, the sign-off one with a required reviewer, and reading
deployments), a clean start (the reset would do nothing; notes it leaves behind and real work don't
count) and `ORCHESTRATOR_MODE` (off fails, observe and shadow warn, enforce passes). It ends with "Ready for the
demo." or "Not ready: {n} check(s) failed." and exits 1 on a failure; a warning alone is ready. The
container can't see the CRM's migrations, so `--crm-head` takes its head; run outside the
container, the check reads `apps/api/migrations` itself. `--smoke --yes` also runs the runners'
`try` on the newest merged real PR and the lowest open real issue: one real, paid call each (about
5 cents), a trial row each, nothing written to GitHub. `scripts/demo.ps1 check [-Smoke]` first
checks from the host that the `db`, `api`, `orchestrator`, `crm-web` and `insights-web` containers
are running and that both web apps answer on `CRM_WEB_PORT` and `INSIGHTS_WEB_PORT` (5173 and 5174
by default; the dev servers refuse requests from inside Docker), then runs the checklist with the
CRM's head from the api container.

**v1 and v2 side by side.** `python -m sdlc.compare_v1 FILE [--since DATE] [--markdown OUT]`
compares v2's decisions with v1's export (`sdlc/v1_export.py` loads and validates it) on the same
PRs, issues and deploys, and reports agreement, every difference and the bar for cutover. It is
read-only; see `docs/v1-comparison.md`.

**v1's record imported.** `python -m sdlc.import_v1 FILE [--apply]` adds every decision in v1's
export to the audit trail with source `v1` (every field as v1 recorded it, `supersedes_id`
remapped), and each approval as a `simulated_approver` row. Implementer, reviewer and trial rows
are left out and counted. A row already imported is skipped, so it can be run again on a newer
export. It is a dry run without `--apply`, and only ever inserts. v2's own runners and measures
read `github` and `synthetic` rows only; the decision log shows imported rows with a "v1" chip.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.
