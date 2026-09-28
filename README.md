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
