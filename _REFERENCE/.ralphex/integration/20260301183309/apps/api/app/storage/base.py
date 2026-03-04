from __future__ import annotations

from typing import Protocol


class StorageNotFoundError(FileNotFoundError):
    """Raised when a storage key cannot be resolved."""


class StorageBackend(Protocol):
    """Storage backend contract for binary payloads addressed by key."""

    def put(self, key: str, data: bytes) -> str:
        """Store bytes at key and return the canonical key."""

    def get(self, key: str) -> bytes:
        """Fetch bytes at key or raise StorageNotFoundError."""

    def delete(self, key: str) -> None:
        """Delete bytes at key if present."""
