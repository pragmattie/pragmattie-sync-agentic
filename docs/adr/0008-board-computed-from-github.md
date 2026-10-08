# 8. The board is computed from GitHub

## Status

Accepted

## Date

2026-10-07

## Context

The delivery board (a GitHub Projects board) is where people see work move. A board kept by hand
drifts from what is really true, and one kept by agents through a database would depend on that
database being up and right.

## Decision

- The board is recomputed from GitHub alone: issue labels, the pull requests that close each
  issue, their `risk-gate` statuses, tier labels and risk comments. The most advanced matching
  column wins.
- A scheduled workflow runs the sync every 10 minutes. It needs no database and writes no audit
  rows.
- It adds missing issues and sets values that differ, and never removes or archives a card.

## Consequences

- The board can't drift: a card moved by hand goes back on the next run. To move work, change
  what is true on GitHub (labels, pull requests, approvals, `/hold`).
- The board keeps working when the orchestrator and its database are off.
- Cards for closed work stay on the board until a person removes them.
