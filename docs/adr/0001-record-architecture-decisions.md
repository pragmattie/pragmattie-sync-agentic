# 1. Record architecture decisions

## Status

Accepted

## Context

This repository is rebuilt from scratch by AI agents across many small issues, each on its own
branch and pull request. Decisions about structure, stack and conventions need a durable, dated
record that later agents and reviewing people can read without reconstructing intent from diffs.

## Decision

We will keep a log of architecture decisions in `docs/adr/`, one Markdown file per decision,
named `NNNN-short-title.md` with a monotonically increasing number. Each record follows this
template: Status, Context, Consequences.

## Consequences

Significant decisions gain a short, permanent record alongside the code they affect. Settled
choices that already live in `CLAUDE.md` (stack, naming, MySQL over Postgres, Vue over React)
are not restated here; new ADRs are for decisions not already captured there.
