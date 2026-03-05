from __future__ import annotations

import subprocess
from pathlib import Path

from ralphite_engine.headless_agent import BackendExecutionConfig, execute_headless_agent


def test_codex_backend_builds_expected_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RALPHITE_DEV_SIMULATED_EXECUTION", raising=False)
    seen: dict[str, object] = {}

    def _fake_run(command, cwd, check, capture_output, text, timeout):  # noqa: ANN001
        seen["command"] = list(command)
        seen["cwd"] = str(cwd)
        seen["timeout"] = timeout
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
    command = seen["command"]
    assert isinstance(command, list)
    assert command[:3] == ["codex", "exec", "--json"]
    assert "--model" in command
    assert "gpt-5.3-codex" in command
    assert 'approval_policy="never"' in command
    assert "--sandbox" in command and "workspace-write" in command
    assert seen["cwd"] == str(tmp_path)


def test_cursor_backend_builds_expected_command(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RALPHITE_DEV_SIMULATED_EXECUTION", raising=False)
    seen: dict[str, object] = {}

    def _fake_run(command, cwd, check, capture_output, text, timeout):  # noqa: ANN001
        seen["command"] = list(command)
        seen["cwd"] = str(cwd)
        return subprocess.CompletedProcess(command, 0, stdout='{"text":"done"}\n', stderr="")

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
    command = seen["command"]
    assert isinstance(command, list)
    assert command[:4] == ["agent", "-p", "--force", "--output-format"]
    assert "--model" in command
    assert "gpt-5.3-codex" in command
    assert seen["cwd"] == str(tmp_path)


def test_backend_binary_missing_is_typed(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.delenv("RALPHITE_DEV_SIMULATED_EXECUTION", raising=False)

    def _fake_run(command, cwd, check, capture_output, text, timeout):  # noqa: ANN001
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
