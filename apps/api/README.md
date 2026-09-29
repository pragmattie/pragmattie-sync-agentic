# apps/api

The PragMattie Sync CRM API: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2. Serves
leads, accounts and contacts, pipeline, and sales forecasting to `apps/crm-web` on port 8000.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.

Migrations live in `migrations/` (Alembic, reading `DATABASE_URL`); this history ignores the
orchestrator's `sdlc_*` tables. See "Database migrations" in the root `README.md`.
