# ci/proposed

Agents cannot edit `.github/workflows/` or `orchestrator/policies/`. When a task needs a new or
changed workflow or policy, the agent writes it here instead and says so in its summary; a
person reviews it and applies it by hand.

Nothing in this folder runs automatically.

## Pending

- **`board.yml`** (4.16): keeps the delivery board current every 10 minutes
  (`python -m sdlc.board sync`, no API key, no Claude call). To apply it:
  1. check the builder app has **Organization → Projects: read and write**;
  2. run `python -m sdlc.board setup --apply` once from `orchestrator/` (4.15), and fix anything it
     reports;
  3. move `ci/proposed/board.yml` to `.github/workflows/board.yml`;
  4. run it once by hand (Actions → Delivery board → Run workflow) and check its log.

  GitHub's built-in project workflows can stay on: the sync sets the same values or corrects them.

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

When a job in `.github/workflows/ci.yml` is renamed, update the `main` ruleset's required status
checks in the same change: they are matched by job name, and a pull request would otherwise wait
on a check that never reports.
