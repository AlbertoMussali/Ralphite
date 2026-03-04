from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


TASK_LINE_PATTERN = re.compile(r"^\s*(?:[-*]|\d+\.)\s+\[([ xX])\]\s+(.*)$")
COMMENT_PATTERN = re.compile(r"<!--\s*(.*?)\s*-->\s*$")
META_KEY_PATTERN = re.compile(r"([A-Za-z_][A-Za-z0-9_-]*)\s*:")


@dataclass(slots=True)
class ParsedTask:
    id: str
    description: str
    line_no: int
    phase: str
    lane: str
    parallel_group: int | None
    depends_on: list[str]
    tools: list[str]
    test: str | None
    agent_profile: str
    completed: bool = False


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


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
        value = meta[value_start:value_end].strip()
        parsed[key] = value
    return parsed


def parse_task_lines(lines: Iterable[str]) -> tuple[list[ParsedTask], list[str]]:
    parsed_tasks: list[ParsedTask] = []
    issues: list[str] = []
    seen_ids: set[str] = set()

    for line_no, raw in enumerate(lines, start=1):
        match = TASK_LINE_PATTERN.match(raw.rstrip("\n"))
        if not match:
            continue

        status = match.group(1)
        payload = match.group(2).strip()

        meta: dict[str, str] = {}
        comment_match = COMMENT_PATTERN.search(payload)
        if comment_match:
            meta = _parse_meta(comment_match.group(1))
            payload = COMMENT_PATTERN.sub("", payload).strip()

        task_id = str(meta.get("id") or "").strip()
        if not task_id:
            issues.append(f"missing required id for task at line {line_no}")
            task_id = f"line_{line_no}"
        if task_id in seen_ids:
            issues.append(f"duplicate task id '{task_id}' at line {line_no}")
            continue
        seen_ids.add(task_id)

        phase = str(meta.get("phase") or meta.get("group") or "phase-1").strip() or "phase-1"
        lane = str(meta.get("lane") or "").strip().lower()
        if not lane:
            seq_flag = str(meta.get("seq") or "").strip().lower()
            lane = "seq_pre" if seq_flag in {"true", "1", "yes"} else "parallel"
        if lane not in {"seq_pre", "parallel", "seq_post"}:
            issues.append(f"invalid lane '{lane}' on task '{task_id}' (line {line_no}); defaulted to parallel")
            lane = "parallel"

        parallel_group: int | None = None
        if "parallel_group" in meta and str(meta.get("parallel_group") or "").strip():
            raw_parallel_group = str(meta.get("parallel_group") or "").strip()
            try:
                parsed_parallel_group = int(raw_parallel_group)
            except ValueError:
                issues.append(
                    f"invalid parallel_group '{raw_parallel_group}' on task '{task_id}' (line {line_no}); ignored"
                )
            else:
                if parsed_parallel_group < 1:
                    issues.append(
                        f"invalid parallel_group '{raw_parallel_group}' on task '{task_id}' (line {line_no}); ignored"
                    )
                else:
                    parallel_group = parsed_parallel_group

        if lane != "parallel" and parallel_group is not None:
            issues.append(
                f"parallel_group is only valid for lane 'parallel' on task '{task_id}' (line {line_no}); ignored"
            )
            parallel_group = None

        task = ParsedTask(
            id=task_id,
            description=payload,
            line_no=line_no,
            phase=phase,
            lane=lane,
            parallel_group=parallel_group,
            depends_on=_split_csv(meta.get("deps")),
            tools=_split_csv(meta.get("tools")),
            test=(meta.get("test") or "").strip() or None,
            agent_profile=(meta.get("agent_profile") or "worker_default").strip() or "worker_default",
            completed=status.lower() == "x",
        )
        parsed_tasks.append(task)

    return parsed_tasks, issues


def parse_task_file(path: Path) -> tuple[list[ParsedTask], list[str]]:
    if not path.exists():
        return [], [f"task file not found: {path}"]
    if not path.is_file():
        return [], [f"task source is not a file: {path}"]
    return parse_task_lines(path.read_text(encoding="utf-8").splitlines())
