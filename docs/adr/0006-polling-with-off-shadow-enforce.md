# 6. Polling, with off/shadow/enforce

## Status

Accepted

## Date

2026-10-06

## Context

The orchestrator has to notice new pull requests, commits, comments and issues. Webhooks need a
public endpoint and a secret, which a locally run demo doesn't have. A new gate also needs a way
to be watched in action before it can block anyone.

## Decision

- The orchestrator polls GitHub rather than receiving webhooks.
- Every GET is conditional (`ETag` / `If-None-Match`); a `304` reuses the remembered response and
  doesn't count against the rate limit. When the limit is spent, polling pauses until it resets.
- One switch, `ORCHESTRATOR_MODE`, sets every agent's mode: `off` does nothing, not even a read;
  `shadow` does everything but the gate always passes and says what it would be; `enforce` makes
  the gate real.
- An agent runs in shadow before it enforces.

## Consequences

- No public endpoint or webhook secret; the service runs anywhere with a token.
- Reaction time is the poll interval, not instant.
- One switch turns every agent off at once, and shadow gives real decisions to judge with no
  risk to merges.
