# PragMattie Sync — agentic rebuild

PragMattie Sync CRM rebuilt from scratch by AI agents, under risk-based human governance.

PragMattie Sync is a fictional SaaS company; its product, people, customers and data are
invented. This repository is a demonstration by **PragMattie Growth Partners, LLC**.

## How the work is done

Each change starts as an issue with a spec. When the issue is released with the `agent-ready`
label, the implementer agent (`.github/workflows/implement.yml`) builds it on its own branch and
opens a pull request as the **PragMattie Builder** app. The governance system from the original
PragMattie Sync then scores the pull request's risk, sets its tier (T0–T3), recommends tests and
gates the release. A person approves every pull request before it merges, and higher tiers ask
more of people: a plan approved before building, a senior sign-off, a deployment approval.
Every agent run is recorded with its model, turns, tokens and cost.

The agent cannot edit its own workflows, the governance policies or any secret: the app has no
permission to, and GitHub refuses such a push.

## What is being built

| Part | What it is |
| --- | --- |
| **PragMattie Sync CRM** | The product: leads, accounts and contacts, pipeline, sales forecast |
| **Delivery Insights** | Development metrics for stakeholders: signals, forecasts, prediction accuracy, the decision log |
| **The delivery board** | A GitHub Projects board in this organization, kept current by the agents |

## Bootstrap (written by people)

These files were written by the project's PM with Claude before any agent ran, and are the only
human-written code here: `CLAUDE.md`, everything under `.github/`, `.gitignore` and this README.
Everything else is agent-built under the governance above.

## Licence

All rights reserved. The code is public to read; no licence to use, copy, modify or distribute it
is granted.

## Development

| Folder | Contents |
| --- | --- |
| `apps/api/` | The CRM API |
| `apps/crm-web/` | The CRM web app |
| `apps/insights-web/` | The Delivery Insights web app |
| `orchestrator/` | The orchestrator service |
| `ci/proposed/` | Workflows an agent has written for a person to review and apply |
| `docs/adr/` | Architecture decision records |

Copy `.env.example` to `.env` and fill in local-only values before running anything. See each
folder's README for what belongs there, and `CLAUDE.md` for the stack, conventions and rules
every agent follows.

The whole stack starts with `docker compose up --build`, and serves:

| Service | Address |
| --- | --- |
| CRM web app (`crm-web`) | <http://localhost:5173> |
| Delivery Insights (`insights-web`) | <http://localhost:5174> |
| CRM API (`api`) | <http://localhost:8000> |
| Orchestrator (`orchestrator`) | <http://localhost:8001> |
| MySQL (`db`) | `localhost:${MYSQL_HOST_PORT:-3307}` |

To bring up just the database, run
`docker compose up -d db`; it publishes on `${MYSQL_HOST_PORT:-3307}` and is healthy once
`docker compose ps` shows it as such.

Every published host port comes from `.env`: `CRM_WEB_PORT`, `INSIGHTS_WEB_PORT`, `API_PORT`,
`ORCH_PORT` and `MYSQL_HOST_PORT`, defaulting to the addresses above. Containers keep their own
ports, and the web apps' API addresses follow the host ports.

## Running beside v1

v2 can run on the same machine as v1 and read this repository, so Delivery Insights shows the
real agent build. It only reads: the agents stay off (`ORCHESTRATOR_MODE=off`), and the
`collector` service sends GET requests only, collecting milestones (as epics), issues, pull
requests and CI jobs every `COLLECT_SECONDS` (900 by default). A failed run is logged and the next
one tries again; a spent rate limit waits until it resets.

v2 reads with its own token, never v1's, so the two never share a rate limit. Create a
fine-grained personal access token for `pragmattie/pragmattie-sync-agentic` only, with these
repository permissions, all **read-only**: Metadata, Contents, Issues, Pull requests, Actions and
Deployments.

1. Create the read-only token above.
2. Copy `.env.example` to `.env` (v2's own, separate from v1's), uncomment the "Running beside
   v1" block (ports 15173, 15174, 18000, 18001 and 13307, and
   `GITHUB_REPO=pragmattie/pragmattie-sync-agentic`), and set the token as `GITHUB_TOKEN`.
3. Run `docker compose --profile collect up -d`. On a new database, apply the migrations first
   (see [Database migrations](#database-migrations)).
4. Open Delivery Insights on <http://localhost:15174>.

## CRM API

The CRM API serves the CRM web app on <http://localhost:8000>, under `/api/v1`, and documents
itself at `/docs` and `/redoc`. [`apps/api/README.md`](apps/api/README.md) covers running it, its
tests, migrations and seed data, its conventions (paging, errors, money and dates), every endpoint,
and how the forecast categories are defined.

## Database migrations

The CRM API and the orchestrator share one MySQL database but keep separate Alembic histories,
each reading `DATABASE_URL`:

| Service | Folder | Sees | Version table |
| --- | --- | --- | --- |
| CRM API | `apps/api/` | every table except `sdlc_*` | `alembic_version` |
| Orchestrator | `orchestrator/` | only `sdlc_*` tables | `sdlc_alembic_version` |

Migrations are not run automatically. With the stack up, apply them with:

```sh
docker compose exec api alembic upgrade head
docker compose exec orchestrator alembic upgrade head
```

The two histories are independent, so either can be upgraded or downgraded first.

To create a migration, change the models, then autogenerate a revision in the owning service:

```sh
docker compose exec api alembic revision --autogenerate -m "add leads"
```

Rename the generated file to `NNNN_short_name.py` (the next number, e.g. `0002_add_leads.py`) and
set its `revision` to the same id (`"0002_add_leads"`), keeping `down_revision` pointing at the
previous one. Review the generated operations before committing; on MySQL, downgrades drop tables
rather than individual indexes. Orchestrator tables must be named `sdlc_*`, or its history will not
see them.

Other useful commands, for either service:

- `alembic check`: fails if the models and the database have drifted apart
- `alembic current`: shows the applied revision
- `alembic downgrade base`: reverts every migration in that service's history
