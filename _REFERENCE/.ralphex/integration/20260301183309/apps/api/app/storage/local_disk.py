from __future__ import annotations

from pathlib import Path

from app.storage.base import StorageNotFoundError


class LocalDiskStorage:
    """Local filesystem-backed storage implementation."""

    def __init__(self, root_path: str) -> None:
        self.root = Path(root_path).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> str:
        destination = self._resolve_key(key)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(data)
        return key

    def get(self, key: str) -> bytes:
        source = self._resolve_key(key)
        if not source.exists() or not source.is_file():
            raise StorageNotFoundError(f"Storage key not found: {key}")
        return source.read_bytes()

    def delete(self, key: str) -> None:
        target = self._resolve_key(key)
        if target.exists():
            target.unlink()

    def _resolve_key(self, key: str) -> Path:
        if not key or key.startswith("/"):
            raise ValueError("Storage key must be a non-empty relative path")
        candidate = (self.root / key).resolve()
        if not self._is_within_root(candidate):
            raise ValueError("Storage key escapes configured root")
        return candidate

    def _is_within_root(self, candidate: Path) -> bool:
        try:
            candidate.relative_to(self.root)
            return True
        except ValueError:
            return False
