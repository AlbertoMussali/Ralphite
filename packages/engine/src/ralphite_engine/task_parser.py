from __future__ import annotations

from dataclasses import dataclass

from ralphite_schemas.plan_v4 import PlanSpecV4


@dataclass(slots=True)
class ParsedTask:
    id: str
    title: str
    description: str
    order: int
    parallel_group: int
    depends_on: list[str]
    agent: str | None
    completed: bool


def parse_plan_tasks(plan: PlanSpecV4) -> tuple[list[ParsedTask], list[str]]:
    issues: list[str] = []
    tasks: list[ParsedTask] = []

    seen_ids: set[str] = set()
    for idx, task in enumerate(plan.tasks):
        if task.id in seen_ids:
            issues.append(f"duplicate task id '{task.id}' at index {idx}")
            continue
        seen_ids.add(task.id)

        group = int(task.parallel_group or 0)
        if group < 0:
            issues.append(f"task '{task.id}' has invalid parallel_group '{group}'")
            group = 0

        tasks.append(
            ParsedTask(
                id=task.id,
                title=task.title,
                description=(task.description or "").strip(),
                order=idx,
                parallel_group=group,
                depends_on=list(task.deps or []),
                agent=task.agent,
                completed=bool(task.completed),
            )
        )

    return tasks, issues
