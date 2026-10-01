from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import accounts, contacts, forecast, health, leads, opportunities, reps, summary

TAGS = [
    {"name": "reps", "description": "The sales reps who own accounts, leads and deals."},
    {"name": "accounts", "description": "Customer companies, with their contacts and pipeline."},
    {"name": "contacts", "description": "People at customer accounts."},
    {"name": "leads", "description": "Prospects being qualified, and their conversion."},
    {"name": "opportunities", "description": "Deals in the sales pipeline."},
    {"name": "forecast", "description": "The quarter's sales forecast by month, rep and stage."},
    {"name": "summary", "description": "Headline figures for the dashboard."},
    {"name": "health", "description": "Whether the API and its database are up."},
]


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="PragMattie Sync CRM API",
        description="Leads, accounts and contacts, pipeline and sales forecasting.",
        openapi_tags=TAGS,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    app.include_router(reps.router)
    app.include_router(accounts.router)
    app.include_router(contacts.router)
    app.include_router(leads.router)
    app.include_router(opportunities.router)
    app.include_router(forecast.router)
    app.include_router(summary.router)
    return app


app = create_app()
