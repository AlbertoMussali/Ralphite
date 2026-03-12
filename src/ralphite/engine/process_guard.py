from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import time
from typing import Any


def managed_process_marker_path(worktree: Path) -> Path:
    return worktree / ".ralphite-managed-process.json"


def write_managed_process_marker(
    worktree: Path, *, pid: int, command: list[str], backend: str
) -> Path:
    marker = managed_process_marker_path(worktree)
    marker.write_text(
        json.dumps(
            {
                "pid": int(pid),
                "command": [str(item) for item in command],
                "backend": str(backend or ""),
                "recorded_at": time.time(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return marker


def read_managed_process_marker(worktree: Path) -> dict[str, Any] | None:
    marker = managed_process_marker_path(worktree)
    if not marker.exists():
        return None
    try:
        payload = json.loads(marker.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    return payload if isinstance(payload, dict) else None


def clear_managed_process_marker(worktree: Path) -> None:
    marker = managed_process_marker_path(worktree)
    if marker.exists():
        marker.unlink()


def process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def terminate_process_tree(pid: int, *, grace_seconds: float = 1.0) -> bool:
    if pid <= 0:
        return False
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        deadline = time.time() + max(0.1, grace_seconds)
        while time.time() < deadline:
            if not process_is_running(pid):
                return True
            time.sleep(0.05)
        return not process_is_running(pid)

    terminated = False
    try:
        pgid = os.getpgid(pid)
    except OSError:
        pgid = None
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGTERM)
            terminated = True
        except OSError:
            pass
    if not terminated:
        try:
            os.kill(pid, signal.SIGTERM)
            terminated = True
        except OSError:
            pass
    deadline = time.time() + max(0.1, grace_seconds)
    while time.time() < deadline:
        if not process_is_running(pid):
            return True
        time.sleep(0.05)
    if pgid is not None:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            pass
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    return not process_is_running(pid)


def cleanup_managed_process_marker(
    worktree: Path, *, grace_seconds: float = 1.0
) -> dict[str, Any]:
    payload = read_managed_process_marker(worktree)
    marker = managed_process_marker_path(worktree)
    if payload is None:
        if marker.exists():
            clear_managed_process_marker(worktree)
            return {"marker_removed": True, "process_terminated": False, "pid": None}
        return {"marker_removed": False, "process_terminated": False, "pid": None}

    pid = payload.get("pid")
    terminated = False
    if isinstance(pid, int) and process_is_running(pid):
        terminated = terminate_process_tree(pid, grace_seconds=grace_seconds)
    clear_managed_process_marker(worktree)
    return {
        "marker_removed": True,
        "process_terminated": terminated,
        "pid": pid if isinstance(pid, int) else None,
        "backend": str(payload.get("backend") or ""),
    }
