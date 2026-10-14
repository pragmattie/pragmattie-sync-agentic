# 10. Observe mode: v2's agents watch beside v1

## Status

Accepted

## Date

2026-10-14

## Context

v1 governs this repository until cutover (rebuild item 7.5): it posts the risk comment, the
`risk-gate` and `release-gate` statuses and the `tier:` and triage labels. Before v2 takes over,
its agents should run against the same pull requests and issues so their decisions can be compared
with v1's, PR by PR (7.2). Shadow mode ([ADR 0006](0006-polling-with-off-shadow-enforce.md)) can't
do this: it writes to GitHub, so the two systems would fight over the same comment, statuses and
labels.

## Decision

- `ORCHESTRATOR_MODE` gains a fourth value, `observe`: `off`, `observe`, `shadow`, `enforce`.
- In observe every agent (PR risk, test selector, triage, release gate, forecaster, and the planner
  when `PLANNER_MODE=on`) polls and decides exactly as in shadow and records the same audit rows,
  but **every GitHub write is skipped**: the one place that writes returns
  `{"skipped": "mode is observe"}` and sends no request at all.
- The gates say what they would post: "Observe (would be {x})" in the audit row and
  `sdlc_gate_status`. The risk comment is rendered with an "Observe mode" banner and kept in the
  audit row's `action_taken`.
- Observe must not cause what an unwritten write would: a `retriage` label is honoured once per
  issue version; labels differing from the triage agent's last answer are not a person's
  correction; the status, tier label, comment re-render and give-up comment are not re-sent each
  poll, and the skipped status is noted once a commit.
- The poll loop runs as the `agents` Compose service (profile `agents`), off by default. Beside
  v1 it runs in observe with v2's read-only token.

## Consequences

- v2's agents can run on the real repository from 7.1, with no risk to v1's governance, and their
  decisions are in v2's audit trail for 7.2 to compare.
- Paid calls stay bounded as before: risk is scored once a commit, triage once an issue version,
  within the per-run and daily caps.
- Observe still reads GitHub every poll; the reads are conditional, so unchanged ones cost no rate
  limit.
- A person never sees observe's decisions on GitHub; Delivery Insights shows them, marked observe.
- ADR 0006's list of modes is extended by this record.
