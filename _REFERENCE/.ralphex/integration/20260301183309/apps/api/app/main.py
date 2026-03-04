from __future__ import annotations

from fastapi import FastAPI
from fastapi.requests import Request
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import Response

from app.audit import AuditLoggingMiddleware
from app.auth import authorize_entries_request
from app.db import initialize_schema
from app.routers.auth import router as auth_router
from app.routes.entries import router as entries_router
from app.settings import get_settings


def create_app(audit_session_factory: sessionmaker[Session] | None = None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="VoiceVault API", version=settings.api_version)
    app.add_middleware(AuditLoggingMiddleware, audit_session_factory=audit_session_factory)

    @app.on_event("startup")
    def startup() -> None:
        # The MVP uses Postgres + Alembic for real environments. For unit tests, we
        # run against SQLite and create tables from SQLAlchemy metadata.
        if settings.database_url.startswith("sqlite"):
            initialize_schema()

    @app.middleware("http")
    async def entry_authorization_middleware(request: Request, call_next) -> Response:
        if request.url.path.startswith("/api/v1/entries"):
            unauthorized = authorize_entries_request(request, settings)
            if unauthorized is not None:
                return unauthorized
        return await call_next(request)

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/version")
    def version() -> dict[str, str]:
        return {"version": settings.api_version}

    app.include_router(auth_router)
    app.include_router(entries_router)

    return app


app = create_app()

