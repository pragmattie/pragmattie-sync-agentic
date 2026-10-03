# ci/proposed

Agents cannot edit `.github/workflows/`. When a task needs a new or changed workflow, the agent
writes it here instead and says so in its summary; a person reviews it and applies it by hand.

Nothing in this folder runs automatically.

## Pending

- **`ci.yml`** adds one job to the live `.github/workflows/ci.yml`, **"Insights web (tests +
  build)"**, built like "CRM web (tests + build)": `npm ci`, `npm test` and `npm run build` in
  `apps/insights-web`. Nothing else differs from the live workflow. To apply it, a person copies it
  over `.github/workflows/ci.yml`, deletes this copy and moves this entry to "Applied", then adds
  "Insights web (tests + build)" to the `main` ruleset's required status checks.

## Applied

- **`ci.yml`** (lint, tests and migration checks) was applied as `.github/workflows/ci.yml`. The
  proposal copy has been deleted so there is only one CI file. To change CI, an agent writes the
  new version of that workflow here and a person applies it over the existing one.

When a job in `.github/workflows/ci.yml` is renamed, update the `main` ruleset's required status
checks in the same change: they are matched by job name, and a pull request would otherwise wait
on a check that never reports.
