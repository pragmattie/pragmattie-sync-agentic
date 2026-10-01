# apps/api

The PragMattie Sync CRM API: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2. Serves
leads, accounts and contacts, pipeline, and sales forecasting to `apps/crm-web` on port 8000.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.

Migrations live in `migrations/` (Alembic, reading `DATABASE_URL`); this history ignores the
orchestrator's `sdlc_*` tables. See "Database migrations" in the root `README.md`.

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
