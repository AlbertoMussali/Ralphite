from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from jwt import InvalidTokenError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import (
    build_access_token,
    create_refresh_session,
    decode_token,
    hash_password,
    hash_token,
    normalize_email,
    utcnow,
    verify_password,
)
from app.db import get_db
from app.models import RefreshSession, User
from app.settings import Settings, get_settings


router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class CredentialsRequest(BaseModel):
    email: str
    password: str = Field(min_length=8, max_length=128)


class TokenRequest(BaseModel):
    refresh_token: str


class AuthTokensResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


def _invalid_credentials() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid email or password.",
    )


@router.post("/signup", response_model=AuthTokensResponse, status_code=status.HTTP_201_CREATED)
def signup(
    payload: CredentialsRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthTokensResponse:
    email = normalize_email(payload.email)
    existing = db.scalar(select(User).where(User.email == email))
    if existing is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email is already registered.")

    user = User(email=email, password_hash=hash_password(payload.password))
    db.add(user)
    db.flush()
    refresh_token, _ = create_refresh_session(db, user, settings)
    db.commit()

    return AuthTokensResponse(
        access_token=build_access_token(user.id, settings),
        refresh_token=refresh_token,
    )


@router.post("/login", response_model=AuthTokensResponse)
def login(
    payload: CredentialsRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthTokensResponse:
    email = normalize_email(payload.email)
    user = db.scalar(select(User).where(User.email == email))
    if user is None or not verify_password(payload.password, user.password_hash):
        raise _invalid_credentials()

    refresh_token, _ = create_refresh_session(db, user, settings)
    db.commit()
    return AuthTokensResponse(
        access_token=build_access_token(user.id, settings),
        refresh_token=refresh_token,
    )


@router.post("/refresh", response_model=AuthTokensResponse)
def refresh(
    payload: TokenRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> AuthTokensResponse:
    try:
        decoded = decode_token(payload.refresh_token, settings, expected_type="refresh")
        user_id = uuid.UUID(str(decoded["sub"]))
        session_id = uuid.UUID(str(decoded["sid"]))
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.") from None

    session = db.get(RefreshSession, session_id)
    now = utcnow()
    if (
        session is None
        or session.user_id != user_id
        or session.token_hash != hash_token(payload.refresh_token)
        or session.revoked_at is not None
        or session.expires_at <= now
    ):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")

    session.revoked_at = now
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")

    new_refresh_token, _ = create_refresh_session(db, user, settings)
    db.commit()

    return AuthTokensResponse(
        access_token=build_access_token(user.id, settings),
        refresh_token=new_refresh_token,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    payload: TokenRequest,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> None:
    try:
        decoded = decode_token(payload.refresh_token, settings, expected_type="refresh")
        session_id = uuid.UUID(str(decoded["sid"]))
    except (InvalidTokenError, KeyError, TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.") from None

    session = db.get(RefreshSession, session_id)
    if session is None or session.token_hash != hash_token(payload.refresh_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid refresh token.")

    if session.revoked_at is None:
        session.revoked_at = utcnow()
        db.commit()
