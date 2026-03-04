from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

import jwt
from passlib.context import CryptContext

from ralphite_api.core.config import settings

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    return pwd_context.verify(password, password_hash)


def make_access_token(user_id: str) -> str:
    exp = datetime.now(UTC) + timedelta(seconds=settings.access_token_ttl_seconds)
    payload = {"sub": user_id, "type": "access", "exp": exp}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def make_refresh_token(user_id: str) -> str:
    exp = datetime.now(UTC) + timedelta(seconds=settings.refresh_token_ttl_seconds)
    payload = {"sub": user_id, "type": "refresh", "exp": exp}
    return jwt.encode(payload, settings.secret_key, algorithm="HS256")


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.secret_key, algorithms=["HS256"])


def make_csrf_token() -> str:
    return secrets.token_urlsafe(24)


def make_runner_token() -> str:
    return secrets.token_urlsafe(36)
