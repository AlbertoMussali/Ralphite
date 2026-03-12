from __future__ import annotations

from pathlib import Path

import yaml

from ralphite.engine.validation import parse_plan_with_defaults, validate_plan_content


def _write_defaults(path: Path) -> None:
    payload = {
        "version": 1,
        "agents": [
            {
                "id": "worker_default",
                "role": "worker",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "system_prompt": "Worker {{task_id}} in {{worktree}}.",
                "tools_allow": ["tool:*"],
            },
            {
                "id": "orchestrator_default",
                "role": "orchestrator",
                "provider": "codex",
                "model": "gpt-5.3-codex",
                "system_prompt": "Orchestrator {{behavior_kind}}.",
                "tools_allow": ["tool:*"],
            },
        ],
        "behaviors": [
            {
                "id": "prepare_default",
                "kind": "prepare_dispatch",
                "agent": "orchestrator_default",
                "prompt_template": "Prepare {{plan_id}}",
                "enabled": True,
            },
            {
                "id": "merge_default",
                "kind": "merge_and_conflict_resolution",
                "agent": "orchestrator_default",
                "prompt_template": "Merge {{behavior_kind}}",
                "enabled": True,
            },
            {
                "id": "summarize_default",
                "kind": "summarize_work",
                "agent": "orchestrator_default",
                "prompt_template": "Summarize {{plan_name}}",
                "enabled": True,
            },
        ],
    }
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False), encoding="utf-8"
    )


def _base_plan(*, defaults_ref: str) -> dict:
    return {
        "version": 1,
        "plan_id": "defaults_plan",
        "name": "defaults_plan",
        "agent_defaults_ref": defaults_ref,
        "materials": {
            "autodiscover": {"enabled": False, "path": ".", "include_globs": []},
            "includes": [],
            "uploads": [],
        },
        "constraints": {"max_parallel": 2},
        "agents": [],
        "tasks": [{"id": "t1", "title": "Do work", "completed": False}],
        "orchestration": {
            "template": "general_sps",
            "inference_mode": "mixed",
            "behaviors": [],
            "branched": {"lanes": ["lane_a", "lane_b"]},
            "blue_red": {"loop_unit": "per_task"},
            "custom": {"cells": []},
        },
        "outputs": {"required_artifacts": []},
    }


def test_plan_with_defaults_ref_resolves_and_validates(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    _write_defaults(defaults)
    plan_path = tmp_path / "plan.yaml"
    plan = _base_plan(defaults_ref="defaults.yaml")
    content = yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)
    plan_path.write_text(content, encoding="utf-8")

    valid, issues, summary = validate_plan_content(
        content,
        workspace_root=tmp_path,
        plan_path=str(plan_path),
    )
    assert valid is True, issues
    resolution = summary.get("defaults_resolution", {})
    assert resolution.get("agents_source") == "defaults_ref"
    assert resolution.get("behaviors_source") == "defaults_ref"

    document, meta = parse_plan_with_defaults(
        content,
        workspace_root=tmp_path,
        plan_path=str(plan_path),
    )
    assert meta["agents_source"] == "defaults_ref"
    assert meta["behaviors_source"] == "defaults_ref"
    assert len(document.agents) == 2
    assert len(document.orchestration.behaviors) == 3


def test_missing_defaults_ref_fails_fast(tmp_path: Path) -> None:
    plan = _base_plan(defaults_ref="missing-defaults.yaml")
    plan_path = tmp_path / "plan.yaml"
    content = yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)
    plan_path.write_text(content, encoding="utf-8")

    valid, issues, _summary = validate_plan_content(
        content,
        workspace_root=tmp_path,
        plan_path=str(plan_path),
    )
    assert valid is False
    assert any(issue.get("code") == "defaults.ref_missing" for issue in issues)


def test_invalid_defaults_schema_fails_fast(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    defaults.write_text(
        yaml.safe_dump(
            {"version": 2, "agents": [], "behaviors": []},
            sort_keys=False,
            allow_unicode=False,
        ),
        encoding="utf-8",
    )
    plan_path = tmp_path / "plan.yaml"
    plan = _base_plan(defaults_ref="defaults.yaml")
    content = yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)
    plan_path.write_text(content, encoding="utf-8")

    valid, issues, _summary = validate_plan_content(
        content,
        workspace_root=tmp_path,
        plan_path=str(plan_path),
    )
    assert valid is False
    assert any(issue.get("code") == "defaults.invalid_schema" for issue in issues)


def test_inline_agents_and_behaviors_override_defaults(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    _write_defaults(defaults)
    plan_path = tmp_path / "plan.yaml"
    plan = _base_plan(defaults_ref="defaults.yaml")
    plan["agents"] = [
        {
            "id": "worker_override",
            "role": "worker",
            "provider": "codex",
            "model": "gpt-5.3-codex",
            "tools_allow": ["tool:*"],
        },
        {
            "id": "orchestrator_override",
            "role": "orchestrator",
            "provider": "codex",
            "model": "gpt-5.3-codex",
            "tools_allow": ["tool:*"],
        },
    ]
    plan["orchestration"]["behaviors"] = [
        {
            "id": "merge_inline",
            "kind": "merge_and_conflict_resolution",
            "agent": "orchestrator_override",
            "enabled": True,
        }
    ]
    content = yaml.safe_dump(plan, sort_keys=False, allow_unicode=False)
    plan_path.write_text(content, encoding="utf-8")

    document, meta = parse_plan_with_defaults(
        content,
        workspace_root=tmp_path,
        plan_path=str(plan_path),
    )
    assert meta["agents_source"] == "inline"
    assert meta["behaviors_source"] == "inline"
    assert document.agents[0].id == "worker_override"
    assert document.orchestration.behaviors[0].id == "merge_inline"
