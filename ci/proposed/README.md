# ci/proposed

Agents cannot edit `.github/workflows/`. When a task needs a new or changed workflow, the agent
writes it here instead and says so in its summary; a person reviews it and applies it by hand.

Nothing in this folder runs automatically.

## Applied

- **`ci.yml`** (lint, tests and migration checks) was applied as `.github/workflows/ci.yml`. The
  proposal copy has been deleted so there is only one CI file. To change CI, an agent writes the
  new version of that workflow here and a person applies it over the existing one.

When a job in `.github/workflows/ci.yml` is renamed, update the `main` ruleset's required status
checks in the same change: they are matched by job name, and a pull request would otherwise wait
on a check that never reports.
