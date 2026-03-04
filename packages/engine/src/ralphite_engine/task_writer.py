from __future__ import annotations

from pathlib import Path
import re
from typing import Any


TASK_LINE_PATTERN = re.compile(r"^(\s*(?:[-*]|\d+\.)\s+)\[([ xX])\](\s+.*)$")
COMMENT_PATTERN = re.compile(r"<!--\s*(.*?)\s*-->\s*$")
META_KEY_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:")


def _parse_meta(meta: str) -> dict[str, str]:
    if not meta.strip():
        return {}
    matches = list(META_KEY_PATTERN.finditer(meta))
    if not matches:
        return {}
    parsed: dict[str, str] = {}
    for idx, match in enumerate(matches):
        key = match.group(1).strip().lower()
        value_start = match.end()
        value_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(meta)
        parsed[key] = meta[value_start:value_end].strip()
    return parsed


def mark_tasks_completed(path: Path, task_ids: list[str]) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {
            "ok": False,
            "path": str(path),
            "updated": 0,
            "requested": len(task_ids),
            "missing": sorted(task_ids),
            "reason": "task_source_missing",
        }

    target = {item for item in task_ids if item}
    if not target:
        return {"ok": True, "path": str(path), "updated": 0, "requested": 0, "missing": []}

    lines = path.read_text(encoding="utf-8").splitlines()
    matched: set[str] = set()
    updated = 0
    output: list[str] = []

    for raw in lines:
        line = raw
        task_match = TASK_LINE_PATTERN.match(line)
        if task_match:
            payload = task_match.group(3).strip()
            comment_match = COMMENT_PATTERN.search(payload)
            if comment_match:
                meta = _parse_meta(comment_match.group(1))
                task_id = str(meta.get("id") or "").strip()
                if task_id and task_id in target:
                    matched.add(task_id)
                    if task_match.group(2).lower() != "x":
                        line = f"{task_match.group(1)}[x]{task_match.group(3)}"
                        updated += 1
        output.append(line)

    if updated > 0:
        path.write_text("\n".join(output) + "\n", encoding="utf-8")

    missing = sorted(target - matched)
    return {
        "ok": True,
        "path": str(path),
        "updated": updated,
        "requested": len(target),
        "missing": missing,
    }
