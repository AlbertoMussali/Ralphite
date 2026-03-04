from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from fastapi import Request
import jwt
from jwt import InvalidTokenError
from sqlalchemy.orm import Session
from starlette.responses import JSONResponse, Response

from app.models import RefreshSession, User
from app.settings import Settings


password_hasher = PasswordHasher()


def utcnow() -> datetime:
    return datetime.now(tz=UTC).replace(tzinfo=None)


def normalize_email(email: str) -> str:
    return email.strip().lower()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def build_access_token(user_id: uuid.UUID, settings: Settings) -> str:
    now = datetime.now(tz=UTC)
    payload = {
        "sub": str(user_id),
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=settings.access_token_ttl_minutes)).timestamp()),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm=settings.jwt_algorithm)


def build_refresh_token(user_id: uuid.UUID, session_id: uuid.UUID, expires_at: datetime, settings: Settings) -> str:
    payload = {
        "sub": str(user_id),
        "sid": str(session_id),
        "typ": "refresh",
        "iat": int(datetime.now(tz=UTC).timestamp()),
        "exp": int(expires_at.timestamp()),
    }
    return jwt.encode(payload, settings.auth_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(token: str, settings: Settings, expected_type: str) -> dict[str, str]:
    payload = jwt.decode(token, settings.auth_secret_key, algorithms=[settings.jwt_algorithm])
    token_type = payload.get("typ")
    if token_type != expected_type:
        raise InvalidTokenError(f"Expected {expected_type} token but got {token_type}.")
    return payload


def create_refresh_session(db: Session, user: User, settings: Settings) -> tuple[str, RefreshSession]:
    expires_at = utcnow() + timedelta(days=settings.refresh_token_ttl_days)
    session_id = uuid.uuid4()
    refresh_token = build_refresh_token(user.id, session_id, expires_at, settings)
    refresh_session = RefreshSession(
        id=session_id,
        user_id=user.id,
        token_hash=hash_token(refresh_token),
        expires_at=expires_at,
    )
    db.add(refresh_session)
    return refresh_token, refresh_session


def _unauthorized_response() -> Response:
    return JSONResponse(
        status_code=401,
        content={"detail": "Unauthorized"},
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def authorize_static_bearer_token(request: Request, expected_token: str) -> Response | None:
    token = _extract_bearer_token(request)
    if token is None or token != expected_token:
        return _unauthorized_response()
    return None


def authorize_entries_request(request: Request, settings: Settings) -> Response | None:
    """
    Authorize requests for early `/api/v1/entries` endpoints.

    During MVP bootstrapping, allow either:
    - a static bearer token (`ENTRY_AUTH_TOKEN`) for lightweight local/testing, OR
    - a signed JWT access token issued by the auth service.
    """
    token = _extract_bearer_token(request)
    if token is None:
        return _unauthorized_response()

    if token == settings.entry_auth_token:
        return None

    try:
        decode_token(token, settings, expected_type="access")
    except InvalidTokenError:
        return _unauthorized_response()

    return None

