from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
from typing import Any
from uuid import uuid4

from ralphite_engine.models import EventJournalRecord, RunCheckpoint, RunPersistenceState


class RunStore:
    def __init__(self, runs_root: Path) -> None:
        self.runs_root = runs_root
        self.runs_root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        path = self.runs_root / run_id
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _state_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "run_state.json"

    def _events_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "event_log.ndjson"

    def _checkpoint_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "checkpoint.json"

    def _lock_path(self, run_id: str) -> Path:
        return self.run_dir(run_id) / "lock"

    def list_run_ids(self) -> list[str]:
        if not self.runs_root.exists():
            return []
        return sorted([p.name for p in self.runs_root.iterdir() if p.is_dir()])

    def acquire_lock(self, run_id: str) -> bool:
        lock_path = self._lock_path(run_id)
        payload = {
            "pid": os.getpid(),
            "acquired_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)
        return True

    def release_lock(self, run_id: str) -> None:
        lock_path = self._lock_path(run_id)
        if lock_path.exists():
            lock_path.unlink()

    def read_lock(self, run_id: str) -> dict[str, Any] | None:
        lock_path = self._lock_path(run_id)
        if not lock_path.exists():
            return None
        try:
            return json.loads(lock_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def lock_is_stale(self, run_id: str) -> bool:
        payload = self.read_lock(run_id)
        if payload is None:
            return True
        pid = payload.get("pid")
        if not isinstance(pid, int):
            return True
        try:
            os.kill(pid, 0)
            return False
        except OSError:
            return True

    def write_state(self, state: RunPersistenceState) -> None:
        path = self._state_path(state.run_id)
        tmp = self._tmp_path(path)
        tmp.write_text(state.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def load_state(self, run_id: str) -> RunPersistenceState | None:
        path = self._state_path(run_id)
        if not path.exists():
            return None
        try:
            return RunPersistenceState.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None

    def append_event(self, run_id: str, event: dict[str, Any]) -> EventJournalRecord:
        seq = int(event.get("id", 0))
        record = EventJournalRecord(seq=seq, ts=str(event.get("ts", "")), run_id=run_id, payload=event)
        path = self._events_path(run_id)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(record.model_dump_json())
            handle.write("\n")
        return record

    def load_events(self, run_id: str) -> list[dict[str, Any]]:
        path = self._events_path(run_id)
        if not path.exists():
            return []

        out: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                record = EventJournalRecord.model_validate_json(line)
                out.append(record.payload)
            except Exception:  # noqa: BLE001
                continue
        out.sort(key=lambda item: int(item.get("id", 0)))
        return out

    def write_checkpoint(self, checkpoint: RunCheckpoint) -> None:
        path = self._checkpoint_path(checkpoint.run_id)
        tmp = self._tmp_path(path)
        tmp.write_text(checkpoint.model_dump_json(indent=2), encoding="utf-8")
        tmp.replace(path)

    def load_checkpoint(self, run_id: str) -> RunCheckpoint | None:
        path = self._checkpoint_path(run_id)
        if not path.exists():
            return None
        try:
            return RunCheckpoint.model_validate_json(path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return None
    def _tmp_path(self, path: Path) -> Path:
        token = f"{os.getpid()}-{threading.get_ident()}-{uuid4().hex}"
        return path.with_name(f"{path.name}.{token}.tmp")
