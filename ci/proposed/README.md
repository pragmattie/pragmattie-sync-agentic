# ci/proposed

Agents cannot edit `.github/workflows/` or `orchestrator/policies/`. When a task needs a new or
changed workflow or policy, the agent writes it here instead and says so in its summary; a
person reviews it and applies it by hand.

Nothing in this folder runs automatically.

## Pending

- **`tiers.yaml`** (6.3): the whole live policy with a `release_gate:` section added (the
  release gate's environments, failure-rate hold and simulated sign-off delay). Its values equal
  the defaults in `sdlc/tiers.py`, so behaviour is the same before and after. Apply it over
  `orchestrator/policies/tiers.yaml` and delete this copy; the tests read this copy while it
  exists, else the live one.
- **`deploy.yml`** (6.5): the deploy workflow. On every push to `main`, the job "Release gate"
  waits (up to 60 minutes, then fails: no verdict, no deploy) for the `release-gate` commit
  status. A success deploys, T3 to `production-signoff` and anything else to `production`; a
  failure stops; pending keeps waiting. The job "Deploy (no-op)" deploys nothing, but GitHub
  records a real deployment, so merged work reaches Production. Until 7.1 v1 posts the
  `release-gate` status; after it, this repository's release runner (`sdlc.release_runner`)
  does. Copy it to `.github/workflows/deploy.yml` and delete this copy; its test reads this copy
  while it exists, else the live one. Before applying it, in Settings > Environments add:
  - `production`, with no protection rules;
  - `production-signoff`, with the repository owner as required reviewer and "Prevent
    self-review" off, so a T3 deploy waits for a person to press "Approve deployment".

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

When a job in `.github/workflows/ci.yml` is renamed, update the `main` ruleset's required status
checks in the same change: they are matched by job name, and a pull request would otherwise wait
on a check that never reports.
