# ci/proposed

Agents cannot edit `.github/workflows/`. When a task needs a new or changed workflow, the agent
writes it here instead and says so in its summary; a person reviews it and applies it by hand.

Nothing in this folder runs automatically.

## `ci.yml`: lint, tests and migration checks

Runs on every pull request and on pushes to `main`. It needs only `contents: read`, uses no
secrets, and cancels a run when a newer commit supersedes it. Each job runs the same commands a
developer runs locally:

| Job | Folder | Commands |
| --- | --- | --- |
| **CRM API (lint + tests)** | `apps/api/` | `pip install -r requirements-dev.txt`, `ruff check .`, `ruff format --check .`, `pytest` |
| **Orchestrator (lint + tests)** | `orchestrator/` | the same as the CRM API |
| **CRM web (tests + build)** | `apps/crm-web/` | `npm ci`, `npm test`, `npm run build` (Node 22, as in its Dockerfile) |
| **Migrations on MySQL** | both services | `alembic upgrade head`, `alembic check`, `alembic downgrade base` |

The Python tests use SQLite, so only the migrations job needs a database. It starts a `mysql:8.4`
service container with the same `pragmattie_sync` database, user and default password as
`docker-compose.yml`, and points `DATABASE_URL` at it. It runs the CRM API's history (upgrade,
check, downgrade, then upgrade again) and the orchestrator's (upgrade, check, downgrade). Finally
it re-runs `alembic check` for the CRM API, which proves the orchestrator's downgrade left the CRM
tables alone.

## Applying it

1. In a small pull request, copy `ci/proposed/ci.yml` to `.github/workflows/ci.yml`.
2. Once it has run on that pull request, add the four jobs to the `main` ruleset as required
   status checks: `CRM API (lint + tests)`, `Orchestrator (lint + tests)`,
   `CRM web (tests + build)` and `Migrations on MySQL`.

Required checks are matched by job name, so keep the names unchanged. If a job is renamed, update
the ruleset in the same change, or pull requests will wait on a check that never reports.
