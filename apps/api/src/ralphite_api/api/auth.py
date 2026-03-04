from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ralphite_api.api.deps import get_current_user
from ralphite_api.core.config import settings
from ralphite_api.db.session import get_db
from ralphite_api.models import AuditLog, User
from ralphite_api.schemas.auth import LoginRequest, SignupRequest, TokenResponse, UserResponse
from ralphite_api.services.auth import (
    decode_token,
    hash_password,
    make_access_token,
    make_csrf_token,
    make_refresh_token,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key="refresh_token",
        value=token,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=settings.refresh_token_ttl_seconds,
        path="/api/v1/auth",
    )


@router.post("/signup", response_model=TokenResponse)
def signup(payload: SignupRequest, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    existing = db.scalar(select(User).where(User.email == payload.email.lower()))
    if existing:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="email already exists")

    user = User(email=payload.email.lower(), password_hash=hash_password(payload.password), settings_json={})
    db.add(user)
    db.flush()

    access_token = make_access_token(user.id)
    refresh_token = make_refresh_token(user.id)
    csrf_token = make_csrf_token()

    _set_refresh_cookie(response, refresh_token)
    db.add(AuditLog(user_id=user.id, event_type="signup", metadata_json={"email": user.email}))
    db.commit()
    return TokenResponse(access_token=access_token, csrf_token=csrf_token)


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, response: Response, db: Session = Depends(get_db)) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    access_token = make_access_token(user.id)
    refresh_token = make_refresh_token(user.id)
    csrf_token = make_csrf_token()

    _set_refresh_cookie(response, refresh_token)
    db.add(AuditLog(user_id=user.id, event_type="login", metadata_json={}))
    db.commit()
    return TokenResponse(access_token=access_token, csrf_token=csrf_token)


@router.post("/refresh", response_model=TokenResponse)
def refresh(
    response: Response,
    refresh_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> TokenResponse:
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing refresh token")
    try:
        payload = decode_token(refresh_token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token") from exc

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid refresh token")

    user = db.scalar(select(User).where(User.id == payload.get("sub")))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")

    access_token = make_access_token(user.id)
    new_refresh = make_refresh_token(user.id)
    csrf_token = make_csrf_token()

    _set_refresh_cookie(response, new_refresh)
    db.add(AuditLog(user_id=user.id, event_type="token_refresh", metadata_json={"at": datetime.now(UTC).isoformat()}))
    db.commit()
    return TokenResponse(access_token=access_token, csrf_token=csrf_token)


@router.post("/logout")
def logout(response: Response, user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    response.delete_cookie("refresh_token", path="/api/v1/auth")
    db.add(AuditLog(user_id=user.id, event_type="logout", metadata_json={}))
    db.commit()
    return {"ok": True}


@router.get("/me", response_model=UserResponse)
def me(user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse(
        id=user.id,
        email=user.email,
        created_at=user.created_at,
        settings_json=user.settings_json,
    )
