from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from sdlc.config import get_settings
from sdlc.routers import health


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="PragMattie Sync Orchestrator")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(health.router)
    return app


app = create_app()
