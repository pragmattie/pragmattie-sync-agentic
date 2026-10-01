# apps/api

The PragMattie Sync CRM API: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2. Serves
leads, accounts and contacts, pipeline, and sales forecasting to `apps/crm-web` on port 8000.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.

Migrations live in `migrations/` (Alembic, reading `DATABASE_URL`); this history ignores the
orchestrator's `sdlc_*` tables. See "Database migrations" in the root `README.md`.

## Tests

`pytest` runs the whole suite on a throwaway SQLite database, with no MySQL or network needed.
`pytest --cov=app` adds a line-coverage report. `tests/test_end_to_end.py` walks one deal from a
rep's lead to a won deal through the API and checks it on the account, `/summary` and `/forecast`.

## Seed data

`python -m app.seed` fills the database with invented demo data (six reps, 60 accounts, their
contacts and opportunities, and 220 leads), using a fixed random seed and dates relative to today
so the forecast always looks current. Every email and website lives on the reserved `.example`
domain.

- **Default:** adds the data; refuses if the database already has any reps.
- **`--if-empty`:** adds the data only if there are no reps yet, otherwise skips.
- **`--reset`:** deletes all CRM rows, then adds fresh data. On MySQL this also restarts each
  table's ids at 1, so links like `/accounts/1` stay stable between demos.

## API conventions

- **Lists** return `{"items": [...], "total": n}` and take `limit` (1–200, default 25; opportunities
  1–500, default 50) and `offset` (≥ 0). Out-of-range values are a 422. `GET /reps` is a plain array.
- **Default order**, always ending in an id tie-breaker:

  | Endpoint | Order |
  | --- | --- |
  | `GET /accounts` | name, id |
  | `GET /contacts` | last name, first name, id |
  | `GET /leads` | `sort` (default `-created_at`), then id descending |
  | `GET /opportunities` | close date, id |
  | `GET /reps` | name, id |

- **Errors** use FastAPI's `{"detail": ...}` body: 400 for a bad `sort` or `quarter`, 404
  `"<Model> <id> not found"`, 409 for a change the record's state forbids (converted leads), 422
  for validation failures.
- **Money** is a JSON string with exactly two decimal places (`"12500000.00"`); percentages and
  counts are numbers.
- **PATCH** changes only the fields sent. `null` clears an optional field; `null` for a required
  field is a 422.
