# orchestrator

A separate service (Python, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2, httpx, PyYAML) on port
8001. Drives the agentic build process, PR risk scoring and tiering, and the engineering signals
and forecasts that `apps/insights-web` displays.

Its database tables are prefixed `sdlc_`, with their own Alembic history (`sdlc_alembic_version`),
separate from the CRM's. Migrations live in `migrations/` (Alembic, reading `DATABASE_URL`) and
see only `sdlc_*` tables. See "Database migrations" in the root `README.md`.

Simulated engineering history (about six months of sprints, issues and pull requests, every row
`source = "synthetic"`) comes from `sdlc/synth.py`: run `python -m sdlc.synth` to add it,
`--if-empty` to skip when it already exists, or `--reset` to replace it.

Real work (issues, pull requests with their files and reviews, and CI jobs, every row
`source = "github"`) comes from `sdlc/signals/github.py`: set `GITHUB_TOKEN` and `GITHUB_REPO`
in `.env`, then run `python -m sdlc.signals.github`. Re-running updates rows in place.

The demo product backlog (four epics and 40 issues) lives in `backlog/backlog.yaml`.
`python -m sdlc.backlog` lists the labels and issues it would create in `GITHUB_REPO`; add
`--apply` to create them. Existing labels and issue titles are skipped, so it is safe to re-run.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.
