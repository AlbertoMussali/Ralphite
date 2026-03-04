from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ralphite_api.db.session import get_db
from ralphite_api.models import Runner, User
from ralphite_api.services.auth import decode_token


def get_current_user(
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    try:
        payload = decode_token(token)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid access token")

    user = db.scalar(select(User).where(User.id == payload.get("sub")))
    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="user not found")
    return user


def get_runner(
    x_runner_token: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> Runner:
    if not x_runner_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing runner token")

    runner = db.scalar(select(Runner).where(Runner.token == x_runner_token))
    if not runner:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid runner token")

    return runner
