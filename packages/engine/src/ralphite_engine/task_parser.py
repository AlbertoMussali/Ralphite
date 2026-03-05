from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ralphite_schemas.plan_v5 import PlanSpecV5


@dataclass(slots=True)
class ParsedTask:
    id: str
    title: str
    description: str
    order: int
    depends_on: list[str]
    agent: str | None
    completed: bool
    routing_lane: str | None
    routing_cell: str | None
    routing_group: str | None
    routing_team_mode: str | None
    routing_tags: list[str]
    acceptance_commands: list[str]
    acceptance_required_artifacts: list[dict[str, str]]
    acceptance_rubric: list[str]


def parse_plan_tasks(plan: PlanSpecV5) -> tuple[list[ParsedTask], list[str]]:
    issues: list[str] = []
    tasks: list[ParsedTask] = []

    seen_ids: set[str] = set()
    for idx, task in enumerate(plan.tasks):
        if task.id in seen_ids:
            issues.append(f"duplicate task id '{task.id}' at index {idx}")
            continue
        seen_ids.add(task.id)

        required_artifacts: list[dict[str, str]] = []
        for artifact in task.acceptance.required_artifacts:
            required_artifacts.append(
                {
                    "id": artifact.id,
                    "path_glob": artifact.path_glob,
                    "format": artifact.format,
                }
            )

        tasks.append(
            ParsedTask(
                id=task.id,
                title=task.title,
                description=(task.description or "").strip(),
                order=idx,
                depends_on=list(task.deps or []),
                agent=task.agent,
                completed=bool(task.completed),
                routing_lane=(task.routing.lane or None),
                routing_cell=(task.routing.cell or None),
                routing_group=(task.routing.group or None),
                routing_team_mode=(task.routing.team_mode or None),
                routing_tags=list(task.routing.tags or []),
                acceptance_commands=list(task.acceptance.commands or []),
                acceptance_required_artifacts=required_artifacts,
                acceptance_rubric=list(task.acceptance.rubric or []),
            )
        )

    return tasks, issues


def task_acceptance_payload(task: ParsedTask) -> dict[str, Any]:
    return {
        "commands": list(task.acceptance_commands),
        "required_artifacts": [dict(item) for item in task.acceptance_required_artifacts],
        "rubric": list(task.acceptance_rubric),
    }
