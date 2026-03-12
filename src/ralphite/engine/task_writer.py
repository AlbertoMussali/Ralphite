from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def mark_tasks_completed(
    path: Path, task_ids: list[str], *, output_path: Path | None = None
) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {
            "ok": False,
            "path": str(path),
            "updated": 0,
            "requested": len(task_ids),
            "missing": sorted(task_ids),
            "reason": "plan_source_missing",
        }

    target = {item for item in task_ids if item}
    if not target:
        return {
            "ok": True,
            "path": str(path),
            "updated": 0,
            "requested": 0,
            "missing": [],
        }

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return {
            "ok": False,
            "path": str(path),
            "updated": 0,
            "requested": len(target),
            "missing": sorted(target),
            "reason": "plan_invalid",
        }

    tasks = raw.get("tasks")
    if not isinstance(tasks, list):
        return {
            "ok": False,
            "path": str(path),
            "updated": 0,
            "requested": len(target),
            "missing": sorted(target),
            "reason": "tasks_missing",
        }

    matched: set[str] = set()
    updated = 0
    for task in tasks:
        if not isinstance(task, dict):
            continue
        task_id = str(task.get("id") or "").strip()
        if not task_id or task_id not in target:
            continue
        matched.add(task_id)
        if not bool(task.get("completed", False)):
            task["completed"] = True
            updated += 1

    destination = output_path or path
    if updated > 0:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(
            yaml.safe_dump(raw, sort_keys=False, allow_unicode=False), encoding="utf-8"
        )

    missing = sorted(target - matched)
    return {
        "ok": True,
        "path": str(destination),
        "source_path": str(path),
        "updated": updated,
        "requested": len(target),
        "missing": missing,
    }
