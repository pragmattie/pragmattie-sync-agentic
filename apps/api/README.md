# apps/api

The PragMattie Sync CRM API: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, Pydantic v2. Serves
leads, accounts and contacts, pipeline, and sales forecasting to `apps/crm-web` on port 8000.

Agent-built; see `CLAUDE.md` at the repository root for the rules that govern it.

## Running it

With Docker, from the repository root, `docker compose up --build` starts the API on
<http://localhost:8000> along with MySQL and the other services. It reloads when its code changes.

Without Docker, from this folder, with a MySQL database reachable:

```sh
pip install -r requirements-dev.txt
export DATABASE_URL=mysql+pymysql://pragmattie_sync:pragmattie_sync@localhost:3307/pragmattie_sync
uvicorn app.main:app --reload --port 8000
```

`DATABASE_URL` defaults to the Compose database (`db:3306`); `CORS_ORIGINS` is a comma-separated
list of web origins allowed to call the API. Both can also go in a local `.env` file.

The API documents itself: <http://localhost:8000/docs> (Swagger UI) and
<http://localhost:8000/redoc> list every endpoint, its parameters and every field, grouped by tag,
from the OpenAPI document at `/openapi.json`.

## Tests

`pytest` runs the whole suite on a throwaway SQLite database, with no MySQL or network needed.
`pytest --cov=app` adds a line-coverage report. `ruff check .` and `ruff format --check .` are the
lint checks CI runs. `tests/test_end_to_end.py` walks one deal from a rep's lead to a won deal
through the API and checks it on the account, `/summary` and `/forecast`. `tests/test_openapi.py`
checks that every endpoint has a summary and every field a description, and that the endpoint
table below matches what the API serves.

## Migrations

Migrations live in `migrations/` (Alembic, reading `DATABASE_URL`); this history ignores the
orchestrator's `sdlc_*` tables. With the stack up, apply them with
`docker compose exec api alembic upgrade head`, or run `alembic upgrade head` here. See
"Database migrations" in the root `README.md` for creating and naming new ones.

## Seed data

`python -m app.seed` (or `docker compose exec api python -m app.seed`) fills the database with
invented demo data (six reps, 60 accounts, their contacts and opportunities, and 220 leads), using
a fixed random seed and dates relative to today so the forecast always looks current. Every email
and website lives on the reserved `.example` domain. Run the migrations first.

- **Default:** adds the data; refuses if the database already has any reps.
- **`--if-empty`:** adds the data only if there are no reps yet, otherwise skips.
- **`--reset`:** deletes all CRM rows, then adds fresh data. On MySQL this also restarts each
  table's ids at 1, so links like `/accounts/1` stay stable between demos.

## API conventions

Every endpoint lives under `/api/v1` and speaks JSON.

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
  counts are numbers. Requests may send money as a string or a number.
- **Dates** are ISO 8601: plain dates such as `close_date` are `"2026-12-15"`, and timestamps such
  as `created_at` are `"2026-10-01T14:05:00"`, with no time zone. Quarters are calendar quarters
  written `"2026-Q4"`; months in the forecast are `"2026-10"`. "The current quarter" is the one
  containing today's date in UTC.
- **PATCH** changes only the fields sent. `null` clears an optional field; `null` for a required
  field is a 422.
- **Nothing is deleted** through the API: there are no `DELETE` endpoints.

## Endpoints

Paths are relative to the API's address (e.g. `http://localhost:8000`). `limit` and `offset` are
the paging parameters above; a parameter in braces is part of the path.

| Method | Path | Parameters | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/v1/health` | none | Check the API is up and whether it can reach its database |
| `GET` | `/api/v1/reps` | none | List every sales rep, with region and quarterly quota |
| `GET` | `/api/v1/accounts` | `q`, `industry`, `owner_id`, `limit`, `offset` | List and search accounts, with contact count and open pipeline |
| `POST` | `/api/v1/accounts` | body: `AccountCreate` | Create an account |
| `GET` | `/api/v1/accounts/{account_id}` | `account_id` | Get an account with its contacts and opportunities |
| `PATCH` | `/api/v1/accounts/{account_id}` | `account_id`, body: `AccountUpdate` | Change some of an account's fields |
| `GET` | `/api/v1/contacts` | `account_id`, `q`, `limit`, `offset` | List and search contacts |
| `POST` | `/api/v1/contacts` | body: `ContactCreate` | Add a contact to an account |
| `GET` | `/api/v1/leads` | `q`, `status` (repeatable), `source`, `owner_id`, `sort`, `limit`, `offset` | List, search and sort leads |
| `POST` | `/api/v1/leads` | body: `LeadCreate` | Create a lead |
| `GET` | `/api/v1/leads/{lead_id}` | `lead_id` | Get a lead |
| `PATCH` | `/api/v1/leads/{lead_id}` | `lead_id`, body: `LeadUpdate` | Change some of a lead's fields; converted leads are a 409 |
| `POST` | `/api/v1/leads/{lead_id}/convert` | `lead_id`, body: `LeadConvertRequest` | Turn a lead into an account, a contact and optionally an opportunity |
| `GET` | `/api/v1/opportunities` | `stage` (repeatable), `owner_id`, `account_id`, `close_from`, `close_to`, `q`, `limit`, `offset` | List and filter opportunities by close date |
| `POST` | `/api/v1/opportunities` | body: `OpportunityCreate` | Create an opportunity; probability defaults to its stage's |
| `GET` | `/api/v1/opportunities/{opportunity_id}` | `opportunity_id` | Get an opportunity |
| `PATCH` | `/api/v1/opportunities/{opportunity_id}` | `opportunity_id`, body: `OpportunityUpdate` | Change some of an opportunity's fields, such as moving its stage |
| `GET` | `/api/v1/forecast` | `quarter` (`YYYY-Qn`, default the current quarter) | Get the quarter's forecast, by month, rep and stage |
| `GET` | `/api/v1/summary` | none | Get the dashboard's lead counts, open pipeline and won-this-quarter |

The body schemas, with every field described, are in `/docs`.

## Pipeline stages and forecast categories

Each opportunity is in one stage. A new deal, or one moved to a new stage without a probability
of its own, takes the stage's default probability:

| Stage | Default probability | Open? |
| --- | --- | --- |
| `prospecting` | 10 | yes |
| `qualification` | 25 | yes |
| `proposal` | 50 | yes |
| `negotiation` | 75 | yes |
| `closed_won` | 100 | no |
| `closed_lost` | 0 | no |

`GET /forecast` looks only at opportunities whose `close_date` falls in the quarter, and adds
their amounts into nested categories:

| Category | Definition |
| --- | --- |
| **Won** | `closed_won` deals |
| **Commit** | Won plus `negotiation` deals |
| **Best case** | Commit plus `proposal` deals |
| **Pipeline** | Every open deal (prospecting through negotiation), won excluded |
| **Weighted** | Won plus, for each open deal, amount × probability ÷ 100 |

So won ≤ commit ≤ best case, and `closed_lost` deals count nowhere. `quota` is the sum of every
rep's quarterly quota. `by_month` gives won, commit, best case and weighted for each of the
quarter's three months; `by_rep` gives each rep's quota, won, commit, weighted and
`attainment_pct` (won ÷ quota × 100, to one decimal place, 0 when the quota is 0), highest weighted
first; `by_stage` gives the count and amount for each open stage. Every total is rounded half up to
two decimal places.
