# 2. Risk-based governance tiers

## Status

Accepted

## Date

2026-10-05

## Context

Every change in this repository is built by an agent, and changes differ widely in what they can
break. Treating them all alike either slows a copy fix down to a billing change's pace or lets a
billing change through at a copy fix's. The project needed one rule, readable by people and
agents, for how much review a pull request gets.

## Decision

Every pull request gets a tier, T0 (Auto) to T3 (Critical), from three inputs; the highest tier
that applies wins:

- **Score bands.** A 0–100 risk score lands in the highest band it reaches: T0 from 0, T1 from 20,
  T2 from 50, T3 from 80.
- **Policy floors**, a minimum whatever the score says: billing/auth T3, schema migrations T3,
  pipeline or forecasting T2, and governance files (`.github/workflows/`,
  `orchestrator/policies/`) T2.
- **The docs cap.** A change to docs or config only is capped at T0, applied only when no floor
  matches.

The policy lives in `orchestrator/policies/tiers.yaml`, checked by a loader that refuses a bad
file, so it changes without a code change. Agents can't edit it: a change is proposed in
`ci/proposed/` and a person applies it.

## Consequences

- Review effort follows risk: a T0 change merges on the AI reviewer's approval, a T3 change
  needs two people.
- Floors make the riskiest areas impossible to score down, whatever the model or the numbers say.
- Changing a band or a floor is a person's edit to one YAML file, itself gated at T2.
- See [0003](0003-agents-propose-code-decides.md) for how the score is made and
  [0007](0007-tier-overrides.md) for changing a tier on one pull request.
