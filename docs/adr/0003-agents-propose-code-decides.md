# 3. Agents propose, code decides

## Status

Accepted

## Date

2026-10-06

## Context

The PR risk agent reads a pull request's title, description and diff, which anyone who opens a
pull request can write. A model that both reads that text and decides the outcome could be talked
into a lower tier, and a model that fails or answers nonsense must not open the gate.

## Decision

- The risk score comes from a rubric in code. The model only proposes an adjustment for what the
  numbers miss, and code clamps it to ±15 and the final score to 0–100.
- The tier comes from the policy ([0002](0002-risk-based-governance-tiers.md)), never from the
  model.
- Any failure (an error, a timeout, an invalid answer) falls back to the matching floor tier, or T2
  when no floor applies. It fails closed, never open.
- Model output can't trigger an action: it is validated against a schema and only ever becomes a
  number and a written reason. Only code writes to GitHub.
- Untrusted text is framed as data: the pull request, its description and its diff are passed
  inside tags the prompt names as untrusted, and instructions inside them are ignored.

## Consequences

- A prompt injection can move a score by at most 15 points and can never lower a floor.
- An outage or a bad answer costs a slower review, never a skipped one.
- Every answer is recorded as the model sent it, with whether it was clamped, so a reviewer can
  see what the model wanted and what code allowed.
