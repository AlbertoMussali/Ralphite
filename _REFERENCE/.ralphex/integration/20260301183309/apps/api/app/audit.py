from __future__ import annotations

from collections.abc import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response
from sqlalchemy.orm import Session, sessionmaker

from app.db import get_sessionmaker
from app.models import AuditLog


def classify_event(path: str) -> str | None:
    if path.startswith("/api/v1/auth/"):
        return "auth_event"
    if path == "/api/v1/entries" or path.startswith("/api/v1/entries/"):
        return "entry_event"
    return None


class AuditLoggingMiddleware(BaseHTTPMiddleware):
    """Write content-free audit rows for auth and entry request paths."""

    def __init__(
        self,
        app: object,
        audit_session_factory: sessionmaker[Session] | Callable[[], Session] | None = None,
    ) -> None:
        super().__init__(app)
        self.audit_session_factory = audit_session_factory

    async def dispatch(self, request: Request, call_next: Callable[[Request], object]) -> Response:
        event_type = classify_event(request.url.path)
        status_code = 500
        response: Response | None = None
        try:
            response = await call_next(request)
            status_code = response.status_code
            return response
        finally:
            if event_type is not None:
                self._write_audit_row(
                    event_type=event_type,
                    request=request,
                    status_code=status_code,
                )

    def _write_audit_row(self, event_type: str, request: Request, status_code: int) -> None:
        session_factory = self.audit_session_factory or get_sessionmaker()
        with session_factory() as session:
            session.add(
                AuditLog(
                    event_type=event_type,
                    metadata_json={
                        "method": request.method,
                        "path": request.url.path,
                        "status_code": status_code,
                    },
                )
            )
            session.commit()

