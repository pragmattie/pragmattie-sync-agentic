# 4. Append-only audit trail

## Status

Accepted

## Date

2026-10-05

## Context

Agents decide tiers, triage issues, build and review code. People approving that work, and the
client watching it, need to see what each agent did, with which model and at what cost, and trust
that the record wasn't rewritten afterwards.

## Decision

- Every agent run is one row in `sdlc_agent_decisions`.
- Rows are never updated or deleted. A correction is a new row pointing at the one it replaces.
- Every run records its model, prompt version, tokens and cost, alongside the agent, its version
  and what it decided about.

## Consequences

- The decision log in Delivery Insights reads straight from this table, with run, token and cost
  totals.
- History is complete: a superseded decision stays visible next to the one that replaced it.
- The table only grows, and code that "fixes" a row must instead write a new one.
