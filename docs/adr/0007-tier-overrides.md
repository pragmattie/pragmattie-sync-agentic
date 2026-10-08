# 7. `/tier` overrides

## Status

Accepted

## Date

2026-10-06

## Context

The risk score is a model of risk, and a person sometimes knows better. They need a way to change
one pull request's tier without editing the policy, and that way must not become a way round it.

## Decision

A person comments `/tier T0`–`/tier T3` on the pull request, and code rules on it:

- Raising is always allowed and sticks across later commits.
- Lowering needs a written reason and covers only the commit it was made on; a new commit is
  tiered afresh.
- Floors can't be argued down: a lowering below a policy floor is rejected.
- Every command, accepted or rejected, is recorded as an audit row
  ([0004](0004-append-only-audit-trail.md)) and shown in the risk comment.

## Consequences

- Caution is cheap and lasting; a lowering is deliberate, explained and short-lived.
- Billing/auth, migrations, pipeline and forecasting keep their floors whoever asks.
- The decision log shows who changed which tier and why.
