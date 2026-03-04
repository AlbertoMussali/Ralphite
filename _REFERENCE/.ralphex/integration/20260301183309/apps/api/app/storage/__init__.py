from __future__ import annotations

from functools import lru_cache

from app.settings import get_settings
from app.storage.base import StorageBackend
from app.storage.local_disk import LocalDiskStorage


@lru_cache(maxsize=1)
def get_storage_backend() -> StorageBackend:
    """Build and cache the configured storage backend."""
    settings = get_settings()
    if settings.storage_backend == "local":
        return LocalDiskStorage(root_path=settings.storage_local_root)
    raise ValueError(f"Unsupported storage backend: {settings.storage_backend}")
