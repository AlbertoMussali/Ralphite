from __future__ import annotations

import re
from typing import Mapping


WORKER_PLACEHOLDER_TOKENS: frozenset[str] = frozenset(
    {
        "plan_id",
        "plan_name",
        "agent_id",
        "agent_role",
        "node_id",
        "task_id",
        "task_title",
        "phase",
        "lane",
        "cell_id",
        "worktree",
        "acceptance_summary",
    }
)

ORCHESTRATOR_PLACEHOLDER_TOKENS: frozenset[str] = frozenset(
    set(WORKER_PLACEHOLDER_TOKENS) | {"behavior_id", "behavior_kind"}
)

_PLACEHOLDER_BLOCK_RE = re.compile(r"{{(.*?)}}", flags=re.DOTALL)
_PLACEHOLDER_TOKEN_RE = re.compile(r"^[a-z_][a-z0-9_]*$")
_PLACEHOLDER_RENDER_RE = re.compile(r"{{\s*([a-z_][a-z0-9_]*)\s*}}")


def validate_prompt_template(
    template: str | None, *, allowed_tokens: set[str] | frozenset[str]
) -> list[str]:
    value = str(template or "")
    errors: list[str] = []
    if "{{" in value or "}}" in value:
        if value.count("{{") != value.count("}}"):
            errors.append("unbalanced placeholder braces")
    for match in _PLACEHOLDER_BLOCK_RE.finditer(value):
        raw = str(match.group(1) or "")
        token = raw.strip()
        if not token:
            errors.append("empty placeholder token")
            continue
        if not _PLACEHOLDER_TOKEN_RE.fullmatch(token):
            errors.append(f"invalid placeholder token '{token}'")
            continue
        if token not in allowed_tokens:
            errors.append(f"placeholder token '{token}' is not allowed")
    return errors


def render_prompt_template(
    template: str | None,
    *,
    context: Mapping[str, str],
    allowed_tokens: set[str] | frozenset[str],
) -> str:
    value = str(template or "")
    errors = validate_prompt_template(value, allowed_tokens=allowed_tokens)
    if errors:
        raise ValueError("; ".join(errors))

    def _replace(match: re.Match[str]) -> str:
        token = str(match.group(1))
        if token not in context:
            raise ValueError(f"placeholder token '{token}' has no runtime value")
        return str(context[token])

    return _PLACEHOLDER_RENDER_RE.sub(_replace, value)
