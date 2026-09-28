# orchestrator

A separate service (Python, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, httpx, PyYAML) on port
8001. Drives the agentic build process, PR risk scoring and tiering, and the engineering signals
and forecasts that `apps/insights-web` displays.

Its database tables are prefixed `sdlc_`, with their own Alembic history (`sdlc_alembic_version`),
separate from the CRM's.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.
