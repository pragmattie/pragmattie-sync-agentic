<!--
Proposed sections for CLAUDE.md (4.21). Agents can't edit CLAUDE.md: a person adds everything
below this comment to CLAUDE.md under its own "## Governance" heading, then deletes this file and
moves its entry in ci/proposed/README.md to Applied. Links are relative to the repository root.
-->

## Governance

The decisions behind these rules are recorded in [`docs/adr/`](docs/adr/README.md). The policy
itself is `orchestrator/policies/tiers.yaml`; this section describes it and never overrides it.

### Tiers

The highest of the score band, the policy floors and (with no floor) the docs cap wins
([ADR 0002](docs/adr/0002-risk-based-governance-tiers.md),
[ADR 0003](docs/adr/0003-agents-propose-code-decides.md)).

| Tier | Gets it | Needs before merge |
| --- | --- | --- |
| T0 Auto | Score 0–19; docs or config only | CI passing and the AI reviewer's approval |
| T1 Light | Score 20–49 | One person's approval, or the objection window (below) |
| T2 Standard | Score 50–79; pipeline, forecasting or governance files | An approved plan, then one senior person's approval; full suite |
| T3 Critical | Score 80+; billing/auth or a schema migration | An approved plan, then two people (senior and code owner; the second seat is the simulated approver); full suite and manual QA |

- A person's GitHub approval counts as their sign-off box
  ([ADR 0005](docs/adr/0005-sign-off-and-simulated-second-approver.md)).
- If the risk agent fails, the PR gets its floor tier, or T2: it fails closed.

### Overrides and `/hold`

- `/tier TN` on a PR: raising always works and sticks; lowering needs a reason and covers only
  that commit; nothing goes below a floor ([ADR 0007](docs/adr/0007-tier-overrides.md)).
- Every `/tier` command, accepted or rejected, is audited and shown in the risk comment.
- `/hold` stops the objection window: a front-end-only T1 PR the reviewer approved otherwise
  counts as signed off an hour later.

### The simulated approver

- **An agent never records the simulated approval; it is recorded only when the repository owner names the PR in that same message.**
- It fills only T3's second seat and is always shown as simulated, never as a person.

### Modes

- `ORCHESTRATOR_MODE` is `off`, `shadow` or `enforce`, one switch for every agent; shadow comes
  before enforce ([ADR 0006](docs/adr/0006-polling-with-off-shadow-enforce.md)).
- v2's poll loop stays `off` until 7.1. Don't turn it on in a change.
- The one live exception is the board sync, a scheduled workflow that needs no database
  ([ADR 0008](docs/adr/0008-board-computed-from-github.md)).

### Triage autonomy

- The triage agent sets labels and keeps one comment explaining them. Nothing else.
- A label a person changed is their correction and sticks.
- It never closes, assigns or edits an issue's text.

### Evaluation bars

- Triage: module 85%, type 90%, points within one step 70% (priority is reported, not gated).
- Risk score, on pooled generated histories: the T0 incident rate is at most a quarter of the
  overall rate, and the top decile by score holds more than half the incident PRs.
- A miss is a prompt (or weights) fix, never a lower bar or changed data.

### The demo rule

- Every Delivery Insights page says how much of what it shows is simulated.
- Simulated history is calibration data, never a team: don't present it as people or their work.
- Every agent run is an append-only audit row with its model, prompt version, tokens and cost
  ([ADR 0004](docs/adr/0004-append-only-audit-trail.md)).
