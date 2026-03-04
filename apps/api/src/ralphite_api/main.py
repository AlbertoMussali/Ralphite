from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ralphite_api import __version__
from ralphite_api.api import auth_router, bootstrap_router, me_router, projects_router, runner_router, runs_router
from ralphite_api.core.config import settings
from ralphite_api.db.base import Base
from ralphite_api.db.runtime_migrations import apply_runtime_migrations
from ralphite_api.db.session import engine
from ralphite_api.models import entities  # noqa: F401


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, version=__version__)

    origins = [item.strip() for item in settings.cors_origins.split(",") if item.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.on_event("startup")
    def _startup() -> None:
        apply_runtime_migrations(engine)
        Base.metadata.create_all(bind=engine)

    @app.get("/health")
    def health() -> dict:
        return {"ok": True, "version": __version__}

    @app.get("/version")
    def version() -> dict:
        return {"version": __version__}

    app.include_router(auth_router, prefix=settings.api_prefix)
    app.include_router(bootstrap_router, prefix=settings.api_prefix)
    app.include_router(me_router, prefix=settings.api_prefix)
    app.include_router(projects_router, prefix=settings.api_prefix)
    app.include_router(runs_router, prefix=settings.api_prefix)
    app.include_router(runner_router, prefix=settings.api_prefix)

    return app


app = create_app()


def run() -> None:
    import uvicorn

    uvicorn.run("ralphite_api.main:app", host="0.0.0.0", port=8000, reload=True)
