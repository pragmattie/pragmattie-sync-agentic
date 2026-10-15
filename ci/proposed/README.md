# ci/proposed

Agents cannot edit `.github/workflows/` or `orchestrator/policies/`. When a task needs a new or
changed workflow or policy, the agent writes it here instead and says so in its summary; a
person reviews it and applies it by hand.

Nothing in this folder runs automatically.

## Pending

Nothing is pending.

## Applied

- **`ci.yml`** (lint, tests and migration checks) was applied as `.github/workflows/ci.yml`. The
  proposal copy has been deleted so there is only one CI file. To change CI, an agent writes the
  new version of that workflow here and a person applies it over the existing one.
- **`ci.yml`** (3.11): added the job **"Insights web (tests + build)"** (`npm ci`, `npm test`
  and `npm run build` in `apps/insights-web`). Applied over `.github/workflows/ci.yml` and the
  copy deleted; the job is a required check in the `main` ruleset.
- **`tiers.yaml`** (4.1): the governance policy. Moved to `orchestrator/policies/tiers.yaml`
  (the loader's default, `sdlc.tiers.POLICY`) and the copy deleted.
- **`approvers.yaml`** (4.11): the simulated second approver. Moved to
  `orchestrator/policies/approvers.yaml` (the loader's default, `sdlc.approver.POLICY`) and the
  copy deleted.
- **`board.yml`** (4.16): keeps the delivery board current every 10 minutes. Moved to
  `.github/workflows/board.yml`; the builder app has Organization → Projects: read and write.
- **`CLAUDE.governance.md`** (4.21): added to `CLAUDE.md` as its "## Governance" section,
  with two wording fixes (the tier sentence; the audit bullet moved out of the demo rule), and
  the copy deleted.
- **`ci.yml`** (5.2): the job **"Migrations on MySQL"** (name unchanged) installs
  `orchestrator/requirements-dev.txt` and, between the orchestrator's `alembic check` and
  `alembic downgrade base`, runs `pytest tests/test_migrations.py tests/test_forecaster.py -k mysql`
  (a forecast seed above 2³¹, and a real forecast saved on MySQL). Applied over
  `.github/workflows/ci.yml` and the copy deleted; no required check changed.

- **`board.yml`** (5.8): the delivery board follows events (issues, pull requests, commit
  statuses, and the runs of "Implementer agent", "Reviewer agent (shadow)" and "CI") as well
  as its 10-minute schedule. Applied over `.github/workflows/board.yml` with one addition: the
  job skips pull requests from forks, which get no secrets. The copy is deleted.

- **`deploy.yml`** (6.5): the deploy workflow. Every push to `main` waits (up to 60 minutes) for
  the `release-gate` commit status, then deploys nothing to `production`, or to
  `production-signoff` (which waits for its required reviewer) for T3. Applied as
  `.github/workflows/deploy.yml` after both environments were created, and the copy deleted.

- **`tiers.yaml`** (6.3): the `release_gate:` section (environments, failure-rate hold, simulated
  sign-off delay), equal to the defaults in `sdlc/tiers.py`, so nothing changed in behaviour.
  Applied over `orchestrator/policies/tiers.yaml` and the copy deleted.

- **`CLAUDE.modes.md`** (7.1): the "### Modes" subsection of `CLAUDE.md`, adding `observe`
  (with cutover as 7.6, renumbered since). Applied and the copy deleted.
- **`ci.yml`** (7.5): the "Migrations on MySQL" job's `-k mysql` step also runs
  `tests/test_accuracy.py` and `tests/test_signals_github.py`. Applied and the copy deleted.

When a job in `.github/workflows/ci.yml` is renamed, update the `main` ruleset's required status
checks in the same change: they are matched by job name, and a pull request would otherwise wait
on a check that never reports.
- **`CLAUDE.cutover.md`** (7.6): replaced `CLAUDE.md`'s "### Modes" subsection at cutover
  step 7 (2026-10-15: v2 governs in `enforce`, v1 archived) and the copy deleted.
- **`tiers.yaml`** and **`CLAUDE.window.md`** (7.9): applied over `orchestrator/policies/tiers.yaml`
  and `CLAUDE.md` (2026-10-15): every T1 pull request the reviewer approved counts as signed off
  15 minutes later unless a person comments `/hold`. Both copies deleted.
