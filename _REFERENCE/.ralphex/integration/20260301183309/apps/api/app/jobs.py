from __future__ import annotations

from collections.abc import Callable
from typing import Any

from redis import Redis
from rq import Queue
from rq.job import Job

from app.settings import get_redis_url

DEFAULT_QUEUE_NAME = "default"


def run_stub_job(payload: str = "ok") -> dict[str, str]:
    """Minimal job used to verify worker wiring end-to-end."""
    return {"status": "ok", "payload": payload}


def run_transcription_job(entry_id: str, audio_asset_id: str) -> dict[str, str]:
    """Placeholder transcription job until STT integration is implemented."""
    return {"status": "queued", "entry_id": entry_id, "audio_asset_id": audio_asset_id}


JOB_REGISTRY: dict[str, Callable[..., Any]] = {
    "stub.echo": run_stub_job,
    "transcription.process": run_transcription_job,
}


def get_redis_connection() -> Redis:
    """Create a Redis client for queue/worker operations."""
    return Redis.from_url(get_redis_url())


def get_default_queue() -> Queue:
    """Return the default RQ queue."""
    return Queue(name=DEFAULT_QUEUE_NAME, connection=get_redis_connection())


def enqueue_registered_job(job_key: str, *args: Any, **kwargs: Any) -> Job:
    """Enqueue a registered job by stable key."""
    job_func = JOB_REGISTRY.get(job_key)
    if job_func is None:
        raise KeyError(f"Unknown job key: {job_key}")
    return get_default_queue().enqueue(job_func, *args, **kwargs)
