"""One line per product module, as the triage agent describes them to the model."""

from sdlc.tables import MODULES

MODULE_DESCRIPTIONS: dict[str, str] = {
    "leads": "Lead capture, scoring, import, duplicate detection, assignment rules.",
    "accounts": "Accounts, contacts, account hierarchy and merge, account timeline.",
    "pipeline": "Deal stages and the pipeline board, stage history, bulk stage updates.",
    "forecasting": "Quota, weighted forecast, forecast snapshots, rollup math.",
    "integrations": "Email/calendar sync, webhooks, CSV import/export, third-party APIs.",
    "billing_auth": "Sign-in, SSO, roles and permissions, seat billing, plan upgrades.",
    "orchestrator": "The predictive SDLC layer itself: signals, agents, tiers, the dashboard.",
    "platform": "Cross-cutting: API infrastructure, search, job queue, audit logging.",
}

if tuple(MODULE_DESCRIPTIONS) != MODULES:
    raise RuntimeError("MODULE_DESCRIPTIONS must list exactly tables.MODULES, in the same order")
