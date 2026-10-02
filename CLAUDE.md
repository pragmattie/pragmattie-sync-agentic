# CLAUDE.md — PragMattie Sync (agentic rebuild)

Instructions for every agent that works in this repository. Read all of it before you start.
If this file and an issue disagree, stop and say so in your summary rather than guessing.

## What this repository is

- **PragMattie Sync** is a *fictional* SaaS company selling **PragMattie Sync CRM** (leads,
  accounts and contacts, pipeline, sales forecasting) to mid-market B2B sales teams. The company,
  its people, customers and all seed data are invented.
- This repository is a **from-scratch rebuild by AI agents**, governed by the original system
  ("v1"). Every change arrives through an issue a person released to you, and every pull request
  is risk-scored, tiered and approved by a person before it merges.
- It is a client demonstration for **PragMattie Growth Partners, LLC**. The code is public but
  **all rights reserved**: there is no licence granting reuse, and you must not add one.

## The three parts you are building

| Part | What it is | Rules |
| --- | --- | --- |
| **PragMattie Sync CRM** (`apps/crm-web` + `apps/api`) | The product: leads, accounts, pipeline, sales forecast | Looks like a real deployed product. No development pages, menus, metrics or "demo"/"simulated" labels. |
| **Delivery Insights** (`apps/insights-web` + `orchestrator/`) | Development metrics for the PM and stakeholders: engineering signals, delivery forecast, prediction accuracy, decision log, release status, delivery flow | Separate app with its own address. Always says what is simulated. Carries the "fictional demo company, created by PragMattie Growth Partners" credit. |
| **The delivery board** | A GitHub Projects board in the `pragmattie` organization | Real work only. The agents keep it current; no web page duplicates it. |

Never put development information in the CRM, or product screens in Delivery Insights.

## How you work

1. **Build exactly what the issue asks.** Its spec and acceptance criteria are the scope. Anything
   else you notice goes in your summary as a follow-up, never into the change.
2. **Clean room.** Work from the issue's spec and this file. Never read, fetch or copy code from the
   original repository (`geoffsmattie/pragmattie-sync`); its behaviour is described in the specs.
3. **Tests are part of the change.** Add or update tests for what you build, and run the relevant
   suite before you finish. Report honestly whether it passes.
4. **Commit as you go** on the branch you were given, with Conventional Commits messages. Don't push
   and don't open the pull request: the workflow does that.
5. **Never edit** `.github/workflows/`, `orchestrator/policies/`, `CLAUDE.md` or anything that holds
   secrets. If a task needs a workflow, write it to `ci/proposed/` and say so: a person applies it.
6. **Never add** credentials, tokens or keys to any file, and never print environment variables.
7. **Keep changes small and readable.** Match the surrounding code; no speculative abstractions.

## Stack (settled; do not swap)

| Layer | Choice |
| --- | --- |
| CRM API | Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2 (`apps/api`, port 8000) |
| Orchestrator | Python, FastAPI, same libraries, httpx, PyYAML; a **separate service** (`orchestrator/`, port 8001) |
| Database | **MySQL 8.4, not Postgres.** Docker service `db`; host port **3307**; containers use `db:3306` |
| Web apps | **Vue 3, not React**: Vite, **Vuetify** (MIT), Pinia, Vue Router, Chart.js via vue-chartjs |
| Tests and lint | pytest + ruff (line length 100, rules `E,F,I,B,UP`, target py312); Vitest for web |
| Local run | Docker Compose; everything starts with `docker compose up --build` |

## Conventions

- **Names:** database, DB user and default password `pragmattie_sync`. Display names "PragMattie
  Sync" and "PragMattie Sync CRM". Vuetify theme `pragmattieSync`: navy `#2E3F55`, teal `#1B8A94`,
  gold `#E8B35A`.
- **Orchestrator tables are prefixed `sdlc_`**, and its Alembic history uses the version table
  `sdlc_alembic_version`. The CRM's Alembic ignores `sdlc_*`; the orchestrator's sees only `sdlc_*`.
- **Migrations:** `NNNN_short_name.py` with a matching `revision` id. On MySQL, downgrades drop
  tables rather than individual indexes.
- **Every engineering signal row has `source` = `synthetic` or `github`.**
- **Seed data** uses the reserved `.example` domain and never real people or companies.
- **Commits:** Conventional Commits, `type(scope): description`, e.g.
  `feat(api): add lead conversion endpoint`. No attribution lines.

## Governance you are working under

- A pull request's tier (T0 to T3) is set by the PR risk agent from its risk score and policy floors:
  Billing/Auth and schema migrations are always T3; Pipeline and Forecasting at least T2.
- T2 and T3 issues start with a plan that a person approves before you build.
- Every run you make is recorded with its model, turns, tokens and cost.
- A reviewer agent reads every agent pull request against its issue's spec and this file, and
  posts a review ("would approve" or "would request changes"). For a T0 pull request its
  approval is the only review: once CI passes, the pull request merges itself. A T1 pull request
  that changes only the web apps (`apps/crm-web/`, `apps/insights-web/`) and that the reviewer
  approved merges itself an hour later, unless a person comments `/hold`. Anything else needs a
  person's approval, and then merges itself.
- When a person merges your pull request, the next item in the same milestone starts by itself if
  its spec is approved. The next milestone never starts without a person.

## Human-written files

This file, `.github/`, and the bootstrap section of `README.md` were written by the project's
PM with Claude, before any agent ran. Everything else is agent-built under the governance above.
