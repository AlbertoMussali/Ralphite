from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from ralphite.engine.headless_agent import (
    BackendExecutionConfig,
    build_codex_exec_command,
    build_cursor_exec_command,
    build_node_prompt,
    build_worker_subprocess_env,
    execute_headless_agent,
    probe_codex_command,
    probe_cursor_command,
)
from ralphite.engine.structure_compiler import RuntimeNodeSpec


def _disable_sim(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.delenv("RALPHITE_DEV_SIMULATED_EXECUTION", raising=False)


def _sample_worker_node() -> RuntimeNodeSpec:
    return RuntimeNodeSpec(
        id="phase-1::task::t1",
        kind="agent",
        group="phase-1",
        depends_on=[],
        task="Task title",
        agent_profile_id="worker_default",
        role="worker",
        phase="phase-1",
        lane="lane_a",
        cell_id="seq_pre",
        source_task_id="t1",
        acceptance={"commands": ["echo ok"], "required_artifacts": [], "rubric": []},
    )


def _sample_orchestrator_node() -> RuntimeNodeSpec:
    return RuntimeNodeSpec(
        id="phase-1::orchestrator::merge",
        kind="agent",
        group="phase-1",
        depends_on=["phase-1::task::t1"],
        task="merge",
        agent_profile_id="orchestrator_default",
        role="orchestrator",
        phase="phase-1",
        lane="shared",
        cell_id="merge",
        behavior_id="merge_default",
        behavior_kind="merge_and_conflict_resolution",
        behavior_prompt_template="Behavior {{behavior_kind}} for {{plan_id}}",
    )


def test_build_codex_exec_command_contract(tmp_path: Path) -> None:
    command = build_codex_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        reasoning_effort="medium",
        worktree=tmp_path,
        sandbox="read-only",
    )
    assert command[:3] == ["codex", "exec", "--json"]
    assert "--cd" in command
    assert str(tmp_path) in command
    assert "--model" in command
    assert "gpt-5.3-codex" in command
    assert 'approval_policy="never"' in command
    assert "read-only" in command


def test_build_codex_exec_command_wraps_powershell_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralphite.engine.headless_agent.shutil.which",
        lambda name: {
            "codex.ps1": r"C:\Users\alberto.mussali\AppData\Local\Programs\codex\codex.ps1",
            "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        }.get(name),
    )
    command = build_codex_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        reasoning_effort="medium",
    )
    assert command[:7] == [
        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\Users\alberto.mussali\AppData\Local\Programs\codex\codex.ps1",
        "exec",
    ]


def test_build_cursor_exec_command_accepts_quoted_powershell_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralphite.engine.headless_agent.shutil.which",
        lambda name: (
            "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
            if name == "powershell"
            else None
        ),
    )
    command = build_cursor_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        cursor_command='"C:\\Users\\alberto.mussali\\AppData\\Local\\cursor-agent\\agent.ps1"',
        force=True,
    )
    assert command[:6] == [
        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\Users\alberto.mussali\AppData\Local\cursor-agent\agent.ps1",
    ]


def test_probe_codex_command_accepts_powershell_script_on_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "codex.ps1"
    script.write_text("Write-Host ok\n", encoding="utf-8")
    monkeypatch.setattr(
        "ralphite.engine.headless_agent.shutil.which",
        lambda name: {
            "codex.ps1": str(script),
            "powershell": "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        }.get(name),
    )
    ok, detail = probe_codex_command()
    assert ok is True
    assert "powershell.exe" in detail
    assert "codex.ps1" in detail


def test_build_cursor_exec_command_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    command = build_cursor_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        cursor_command="agent",
        force=True,
    )
    assert command[:4] == ["agent", "-p", "--force", "--output-format"]
    assert "--model" in command
    assert "gpt-5.3-codex" in command


def test_build_cursor_exec_command_wraps_powershell_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ralphite.engine.headless_agent.shutil.which",
        lambda name: (
            "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
            if name == "powershell"
            else None
        ),
    )
    command = build_cursor_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        cursor_command=r"C:\Users\alberto.mussali\AppData\Local\cursor-agent\agent.ps1",
        force=True,
    )
    assert command[:6] == [
        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        r"C:\Users\alberto.mussali\AppData\Local\cursor-agent\agent.ps1",
    ]
    assert "-p" in command


def test_probe_cursor_command_accepts_powershell_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = tmp_path / "agent.ps1"
    script.write_text("Write-Host ok\n", encoding="utf-8")
    monkeypatch.setattr(
        "ralphite.engine.headless_agent.shutil.which",
        lambda name: (
            "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
            if name == "powershell"
            else None
        ),
    )
    ok, detail = probe_cursor_command(str(script))
    assert ok is True
    assert "powershell.exe" in detail
    assert str(script) in detail


def test_build_cursor_exec_command_falls_back_to_localappdata_script(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    local_appdata = tmp_path / "LocalAppData"
    script = local_appdata / "cursor-agent" / "agent.ps1"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text("Write-Host ok\n", encoding="utf-8")
    monkeypatch.setenv("LOCALAPPDATA", str(local_appdata))
    monkeypatch.setattr(
        "ralphite.engine.headless_agent.shutil.which",
        lambda name: (
            "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
            if name == "powershell"
            else None
        ),
    )
    command = build_cursor_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        cursor_command="agent",
        force=True,
    )
    assert command[:6] == [
        "C:\\Windows\\System32\\WindowsPowerShell\\v1.0\\powershell.exe",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(script.resolve()),
    ]


def test_build_node_prompt_renders_worker_system_prompt(tmp_path: Path) -> None:
    prompt = build_node_prompt(
        _sample_worker_node(),
        worktree=tmp_path,
        permission_snapshot={
            "allow_tools": ["tool:*"],
            "deny_tools": [],
            "allow_mcps": ["mcp:*"],
            "deny_mcps": [],
        },
        plan_id="demo",
        plan_name="Demo",
        agent_id="worker_default",
        agent_role="worker",
        system_prompt="Execute {{task_id}} in {{worktree}} with {{acceptance_summary}}",
    )
    assert "Execute t1 in" in prompt
    assert "acceptance.commands=1" in prompt


def test_build_node_prompt_renders_orchestrator_behavior_prompt(tmp_path: Path) -> None:
    prompt = build_node_prompt(
        _sample_orchestrator_node(),
        worktree=tmp_path,
        permission_snapshot={
            "allow_tools": ["tool:*"],
            "deny_tools": [],
            "allow_mcps": ["mcp:*"],
            "deny_mcps": [],
        },
        plan_id="demo",
        plan_name="Demo",
        agent_id="orchestrator_default",
        agent_role="orchestrator",
        system_prompt="Review {{behavior_kind}}",
        behavior_prompt_template="Behavior {{behavior_kind}} for {{plan_id}}",
    )
    assert "Review merge_and_conflict_resolution" in prompt
    assert "Behavior merge_and_conflict_resolution for demo" in prompt


def test_build_node_prompt_rejects_invalid_placeholder_token(tmp_path: Path) -> None:
    with pytest.raises(ValueError) as exc:
        build_node_prompt(
            _sample_worker_node(),
            worktree=tmp_path,
            permission_snapshot={
                "allow_tools": ["tool:*"],
                "deny_tools": [],
                "allow_mcps": ["mcp:*"],
                "deny_mcps": [],
            },
            plan_id="demo",
            plan_name="Demo",
            agent_id="worker_default",
            agent_role="worker",
            system_prompt="bad {{behavior_kind}} token",
        )
    assert "not allowed" in str(exc.value)


def test_build_worker_subprocess_env_uses_short_windows_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    temp_root = tmp_path / "temp"
    monkeypatch.setattr("ralphite.engine.headless_agent.os.name", "nt")
    monkeypatch.setattr(
        "ralphite.engine.headless_agent.tempfile.gettempdir", lambda: str(temp_root)
    )
    monkeypatch.setenv("UV_CACHE_DIR", ".uv-cache-win11")
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / ".venv-win11"))
    env = build_worker_subprocess_env(worktree=tmp_path / "deep" / "worktree")
    assert env["UV_CACHE_DIR"].startswith(str(temp_root))
    assert env["UV_PROJECT_ENVIRONMENT"].startswith(str(temp_root))
    assert env["TMP"].startswith(env["UV_CACHE_DIR"])
    assert env["TEMP"].startswith(env["UV_CACHE_DIR"])
    assert "VIRTUAL_ENV" not in env


def test_codex_backend_builds_expected_command(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)
    seen: dict[str, object] = {}

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        seen["command"] = list(command)
        seen["cwd"] = str(cwd)
        seen["timeout"] = timeout
        seen["env"] = dict(env)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","text":"done"}}\n',
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
            timeout_seconds=120,
        ),
        prompt="test prompt",
        worktree=tmp_path,
    )
    assert ok is True
    assert result["backend"] == "codex"
    assert seen["command"] == build_codex_exec_command(
        prompt="test prompt",
        model="gpt-5.3-codex",
        reasoning_effort="medium",
        worktree=tmp_path,
        sandbox="workspace-write",
    )
    assert seen["cwd"] == str(tmp_path)
    assert "VIRTUAL_ENV" not in seen["env"]


def test_cursor_backend_builds_expected_command(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)
    seen: dict[str, object] = {}

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        seen["command"] = list(command)
        seen["cwd"] = str(cwd)
        seen["env"] = dict(env)
        return subprocess.CompletedProcess(
            command, 0, stdout='{"text":"done"}\n', stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="cursor",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
            timeout_seconds=120,
        ),
        prompt="cursor prompt",
        worktree=tmp_path,
    )
    assert ok is True
    assert result["backend"] == "cursor"
    assert seen["command"] == build_cursor_exec_command(
        prompt="cursor prompt",
        model="gpt-5.3-codex",
        cursor_command="agent",
        force=True,
    )
    assert seen["cwd"] == str(tmp_path)
    assert "VIRTUAL_ENV" not in seen["env"]


def test_cursor_plain_text_output_is_accepted(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command, 0, stdout="finished successfully", stderr=""
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="cursor",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="cursor prompt",
        worktree=tmp_path,
    )
    assert ok is True
    assert result["summary"] == "finished successfully"


def test_backend_binary_missing_is_typed(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        raise FileNotFoundError("missing")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="test prompt",
        worktree=tmp_path,
    )
    assert ok is False
    assert result["reason"] == "backend_binary_missing"


def test_backend_timeout_is_typed(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        raise subprocess.TimeoutExpired(
            command, timeout, output="partial", stderr="timed out"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
            timeout_seconds=10,
        ),
        prompt="test prompt",
        worktree=tmp_path,
    )
    assert ok is False
    assert result["reason"] == "backend_timeout"
    assert result["timeout_seconds"] == 10


def test_backend_auth_failure_is_typed(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="Unauthorized: login required"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="test prompt",
        worktree=tmp_path,
    )
    assert ok is False
    assert result["reason"] == "backend_auth_failed"


def test_backend_model_unsupported_is_typed(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="selected model is not supported"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="test prompt",
        worktree=tmp_path,
    )
    assert ok is False
    assert result["reason"] == "backend_model_unsupported"


def test_backend_nonzero_generic_is_typed(monkeypatch, tmp_path: Path) -> None:
    _disable_sim(monkeypatch)

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        return subprocess.CompletedProcess(
            command, 1, stdout="", stderr="generic backend failure"
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="test prompt",
        worktree=tmp_path,
    )
    assert ok is False
    assert result["reason"] == "backend_nonzero"


def test_backend_output_malformed_for_empty_cursor_output(
    monkeypatch, tmp_path: Path
) -> None:
    _disable_sim(monkeypatch)

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="cursor",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="cursor prompt",
        worktree=tmp_path,
    )
    assert ok is False
    assert result["reason"] == "backend_payload_malformed"


def test_backend_out_of_worktree_claim_is_recorded_as_diagnostic(
    monkeypatch, tmp_path: Path
) -> None:
    _disable_sim(monkeypatch)
    outside = tmp_path.parent / "outside.txt"

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        payload = {
            "type": "item.completed",
            "item": {
                "type": "agent_message",
                "text": f"Updated {outside}",
            },
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload) + "\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="test prompt",
        worktree=tmp_path,
    )
    assert ok is True
    diagnostics = result.get("diagnostics", {})
    assert isinstance(diagnostics, dict)
    assert diagnostics.get("external_path_mentioned") is True


def test_cursor_result_payload_does_not_trip_false_out_of_worktree_claim(
    monkeypatch, tmp_path: Path
) -> None:
    _disable_sim(monkeypatch)
    summary = (
        "Created the Phase 1 handoff artifacts.\n\n"
        "Files added:\n"
        "- `docs/PLANS/ralphite/artifacts/phase1_handoff_and_scope.md`\n"
        "- `docs/PLANS/ralphite/artifacts/phase1_scope_decisions.md`\n\n"
        "One note: the Phase 1 YAML references some Phase 0 memory files that are "
        "not present in this worktree, so the handoff cites the Phase 0 artifacts "
        "that do exist here as the durable system of record."
    )

    def _fake_run(command, cwd, env, check, capture_output, text, timeout):  # noqa: ANN001
        payload = {
            "type": "result",
            "subtype": "success",
            "is_error": False,
            "duration_ms": 1000,
            "result": summary,
            "session_id": "session-1",
            "request_id": "request-1",
        }
        return subprocess.CompletedProcess(
            command,
            0,
            stdout=json.dumps(payload) + "\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", _fake_run)
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="cursor",
            model="gpt-5.4-medium",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="cursor prompt",
        worktree=tmp_path,
    )
    assert ok is True
    assert result["summary"] == summary


def test_backend_worktree_missing_is_typed(tmp_path: Path) -> None:
    missing_worktree = tmp_path / "missing"
    ok, result = execute_headless_agent(
        config=BackendExecutionConfig(
            backend="codex",
            model="gpt-5.3-codex",
            reasoning_effort="medium",
            cursor_command="agent",
        ),
        prompt="test prompt",
        worktree=missing_worktree,
    )
    assert ok is False
    assert result["reason"] == "backend_worktree_missing"
