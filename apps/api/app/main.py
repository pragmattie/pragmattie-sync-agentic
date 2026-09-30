from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import accounts, contacts, forecast, health, leads, opportunities, reps


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PragMattie Sync CRM API")
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
    return app


app = create_app()
