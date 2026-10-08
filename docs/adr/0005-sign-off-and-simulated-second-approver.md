# 5. Sign-off and the simulated second approver

## Status

Accepted

## Date

2026-10-06

## Context

Each tier needs sign-off before merge, and that sign-off has to be visible on the pull request.
The project has one person, yet T3 needs two; without a second seat, T3 work could never merge.

## Decision

- The risk comment on each pull request shows sign-off boxes for what its tier needs.
- A person's GitHub approval counts as their sign-off.
- For T0 the AI reviewer's approval is the only review needed.
- The objection window: a T1 pull request that changes only the web apps (`apps/crm-web/`,
  `apps/insights-web/`) and that the AI reviewer approved counts as signed off an hour after that
  approval, unless a person comments `/hold`. A person's approval still merges it at once. T2 and
  T3 are never covered.
- T3's second seat is filled by a simulated approver (`orchestrator/policies/approvers.yaml`). It
  is manual only: it approves nothing by itself, only when the repository owner runs its approve
  command for a named pull request. It is always labelled simulated, never shown as a person.

## Consequences

- Low-risk front-end work flows without waiting for a person, and `/hold` keeps them in control.
- T3 work can merge in a one-person project, with the gap stated openly rather than hidden.
- A real second approver can replace the simulated one by editing the approvers policy.
