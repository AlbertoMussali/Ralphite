from __future__ import annotations

from pathlib import Path
import hashlib
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from jwt import InvalidTokenError
from sqlalchemy.orm import Session

from app.auth import decode_token
from app.db import get_db
from app.jobs import enqueue_registered_job
from app.models import AudioAsset, Entry, User
from app.settings import get_settings
from app.storage import get_storage_backend

router = APIRouter(prefix="/api/v1/entries", tags=["entries"])


def _extract_bearer_token(request: Request) -> str:
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _resolve_request_user_id(request: Request, db: Session) -> uuid.UUID:
    settings = get_settings()
    token = _extract_bearer_token(request)

    if token == settings.entry_auth_token:
        requested_user_id = request.headers.get("x-user-id")
        if requested_user_id is not None:
            try:
                user_id = uuid.UUID(requested_user_id)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="x-user-id must be a valid UUID",
                ) from exc

            if db.get(User, user_id) is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
            return user_id

        first_user_id = db.query(User.id).order_by(User.created_at.asc()).limit(1).scalar()
        if first_user_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No users available for static entry token; provide x-user-id",
            )
        return first_user_id

    try:
        payload = decode_token(token, settings, expected_type="access")
        user_id = uuid.UUID(str(payload["sub"]))
    except (InvalidTokenError, KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc

    if db.get(User, user_id) is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unauthorized",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user_id


@router.post("", status_code=status.HTTP_201_CREATED)
def create_entry(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, str]:
    user_id = _resolve_request_user_id(request, db)
    entry = Entry(user_id=user_id, status="draft")
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return {"entry_id": str(entry.id), "status": entry.status}


@router.get("")
def list_entries() -> dict[str, list[dict[str, str]]]:
    return {"entries": []}


@router.get("/{entry_id}")
def get_entry(entry_id: uuid.UUID) -> dict[str, str]:
    return {"entry_id": str(entry_id)}


@router.post("/{entry_id}/audio", status_code=status.HTTP_201_CREATED)
async def upload_entry_audio(
    entry_id: uuid.UUID,
    request: Request,
    file: UploadFile | None = File(default=None),
    db: Session = Depends(get_db),
) -> dict[str, str | int]:
    entry = db.get(Entry, entry_id)
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Entry not found")

    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        if file is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Missing multipart file field")
        if not (file.content_type or "").startswith("audio/"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Multipart file must have Content-Type audio/*",
            )

        body = await file.read()
        mime_type = file.content_type or "application/octet-stream"
        filename = Path(file.filename or f"entry-{entry_id}.webm").name or f"entry-{entry_id}.webm"
    else:
        if not content_type.startswith("audio/"):
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail="Content-Type must be audio/*",
            )
        body = await request.body()
        mime_type = content_type
        requested_name = request.headers.get("x-audio-filename", f"entry-{entry_id}.webm")
        filename = Path(requested_name).name or f"entry-{entry_id}.webm"

    if not body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Audio payload is empty")

    unique = uuid.uuid4()
    storage_key = f"entries/{entry_id}/audio/{unique}-{filename}"

    storage = get_storage_backend()
    storage.put(storage_key, body)

    sha256_hex = hashlib.sha256(body).hexdigest()
    audio_asset = AudioAsset(
        entry_id=entry_id,
        storage_key=storage_key,
        mime_type=mime_type,
        size_bytes=len(body),
        sha256_hex=sha256_hex,
    )
    entry.status = "transcribing"
    db.add(audio_asset)
    db.commit()
    db.refresh(audio_asset)
    db.refresh(entry)

    try:
        job = enqueue_registered_job(
            "transcription.process",
            entry_id=str(entry_id),
            audio_asset_id=str(audio_asset.id),
        )
    except Exception as exc:
        db.delete(audio_asset)
        entry.status = "draft"
        db.commit()
        storage.delete(storage_key)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to enqueue transcription job",
        ) from exc

    return {
        "asset_id": str(audio_asset.id),
        "entry_id": str(entry_id),
        "storage_key": storage_key,
        "size_bytes": audio_asset.size_bytes,
        "mime_type": audio_asset.mime_type,
        "status": entry.status,
        "job_id": job.id,
    }
