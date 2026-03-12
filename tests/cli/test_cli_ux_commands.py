from __future__ import annotations

import json
from pathlib import Path
import subprocess
import tempfile

import pytest
from ralphite.engine.headless_agent import (
    build_codex_exec_command,
    build_cursor_exec_command,
)
from typer.testing import CliRunner

import ralphite.cli.checks.suites as suite_mod
import ralphite.cli.commands.check_cmd as check_mod
import ralphite.cli.doctoring as doctor_mod
import ralphite.cli.commands.quickstart_cmd as quickstart_mod
import ralphite.cli.commands.recover_cmd as recover_mod
import ralphite.cli.commands.replay_cmd as replay_mod
import ralphite.cli.commands.run_cmd as run_mod
import ralphite.cli.commands.watch_cmd as watch_mod
import ralphite.cli.core as core_mod
from ralphite.cli.cli import app
from ralphite.cli.core import _orchestrator
from ralphite.cli.exit_codes import RECOVER_EXIT_PENDING
from ralphite.engine import LocalOrchestrator
from ralphite.engine.orchestrator import RunStartBlockedError


def _init_repo(path: Path) -> None:
    subprocess.run(
        ["git", "init", "-b", "main"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Ralphite Test"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "ralphite@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    (path / "README.md").write_text("repo\n", encoding="utf-8")
    subprocess.run(
        ["git", "add", "-A"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial"],
        cwd=path,
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture(autouse=True)
def _git_workspace(tmp_path: Path) -> None:
    _init_repo(tmp_path)


@pytest.fixture(autouse=True)
def _stable_backend_command_probes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(doctor_mod, "probe_codex_command", lambda: (True, "codex"))
    monkeypatch.setattr(
        doctor_mod, "probe_cursor_command", lambda command: (True, command)
    )


def _broken_v1_missing_worker(plan_id: str) -> str:
    return f"""
version: 1
plan_id: {plan_id}
name: {plan_id}
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
constraints:
  max_parallel: 1
agents:
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
tasks:
  - id: t1
    title: invalid
    completed: false
orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
outputs:
  required_artifacts: []
"""


def _failing_retained_plan(plan_id: str = "retained-failure") -> str:
    return f"""
version: 1
plan_id: {plan_id}
name: {plan_id}
materials:
  autodiscover:
    enabled: false
    path: .
    include_globs: []
  includes: []
  uploads: []
constraints:
  max_parallel: 1
agents:
  - id: worker_default
    role: worker
    provider: codex
    model: gpt-5.3-codex
    tools_allow: [tool:*]
  - id: orchestrator_default
    role: orchestrator
    provider: codex
    model: gpt-5.3-codex
orchestration:
  template: general_sps
  inference_mode: mixed
  behaviors:
    - id: merge_default
      kind: merge_and_conflict_resolution
      agent: orchestrator_default
      enabled: true
  branched:
    lanes: [lane_a, lane_b]
  blue_red:
    loop_unit: per_task
  custom:
    cells: []
tasks:
  - id: t1
    title: failing acceptance
    completed: false
    acceptance:
      commands:
        - uv run python -c "import sys; sys.exit(1)"
outputs:
  required_artifacts: []
"""


def _create_retained_failure_run(workspace: Path) -> str:
    orch = _orchestrator(workspace)
    run_id = orch.start_run(plan_content=_failing_retained_plan())
    assert orch.wait_for_run(run_id, timeout=8.0)
    run = orch.get_run(run_id)
    assert run is not None
    assert run.status == "failed"
    assert isinstance(run.metadata.get("retained_work"), list)
    assert run.metadata.get("retained_work")
    return run_id


def _delete_run_state_but_keep_git_artifacts(workspace: Path, run_id: str) -> None:
    run_dir = workspace / ".ralphite" / "runs" / run_id
    if run_dir.exists():
        for child in run_dir.iterdir():
            child.unlink()
        run_dir.rmdir()
    history_path = workspace / ".ralphite" / "runs" / "history.json"
    if history_path.exists():
        history_path.unlink()


def test_quickstart_json_output(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code in {0, 1}
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "quickstart"
    assert payload["status"] in {"succeeded", "failed"}


def test_run_json_reports_dirty_worktree_detail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeConfig:
        default_backend = "codex"
        default_model = "gpt-5.3-codex"
        default_reasoning_effort = "medium"

    class _FakeOrchestrator:
        config = _FakeConfig()

        def git_runtime_status(self) -> dict[str, object]:
            return {
                "ok": False,
                "reason": "git_required",
                "detail": "worktree is dirty in a blocking way",
                "remediation": 'git add -A && git commit -m "save state"',
            }

    monkeypatch.setattr(
        run_mod, "_orchestrator", lambda _workspace: _FakeOrchestrator()
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
        ],
    )
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["exit_code"] == 1
    assert payload["issues"][0]["code"] == "git.required"
    assert payload["issues"][0]["message"] == "worktree is dirty in a blocking way"
    assert 'git add -A && git commit -m "save state"' in payload["next_actions"]


def test_run_json_reports_start_preflight_block_and_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeConfig:
        default_backend = "codex"
        default_model = "gpt-5.3-codex"
        default_reasoning_effort = "medium"

    class _FakeOrchestrator:
        config = _FakeConfig()

        def git_runtime_status(self) -> dict[str, object]:
            return {"ok": True, "detail": "clean"}

        def collect_requirements(self, plan_ref=None):  # noqa: ANN001
            return {"tools": [], "mcps": []}

        def start_run(self, **kwargs):  # noqa: ANN003
            raise RunStartBlockedError(
                {
                    "ok": False,
                    "reason": "stale_recovery_state_present",
                    "detail": "workspace has unresolved recoverable runs or stale managed artifacts",
                    "next_commands": [
                        "uv run ralphite history --workspace . --output table"
                    ],
                }
            )

    monkeypatch.setattr(
        run_mod, "_orchestrator", lambda _workspace: _FakeOrchestrator()
    )
    monkeypatch.setattr(
        run_mod,
        "_resolve_plan_ref",
        lambda _orch, _plan_ref: tmp_path / ".ralphite" / "plans" / "starter.yaml",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
            "--first-failure-recovery",
            "agent_best_effort",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert payload["data"]["first_failure_recovery"] == "agent_best_effort"
    assert (
        payload["data"]["run_start_preflight"]["reason"]
        == "stale_recovery_state_present"
    )


def test_recover_allows_dirty_workspace_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeOrchestrator:
        def git_repository_status(self) -> dict[str, object]:
            return {
                "ok": True,
                "detail": "git worktree detected (base branch: main)",
                "dirty": True,
            }

        def git_runtime_status(self) -> dict[str, object]:
            return {
                "ok": False,
                "detail": "worktree is dirty in a blocking way",
                "dirty": True,
                "remediation": 'git add -A && git commit -m "save state"',
            }

        def list_recoverable_runs(self) -> list[str]:
            return ["run-123"]

        def recover_run(self, run_id: str) -> bool:
            return run_id == "run-123"

        def set_recovery_mode(self, run_id: str, mode: str, prompt=None) -> bool:  # noqa: ANN001
            return run_id == "run-123" and mode == "manual"

        def recovery_preflight(self, run_id: str) -> dict[str, object]:
            return {"ok": True, "blocking_reasons": [], "next_commands": []}

        def get_run(self, run_id: str):  # noqa: ANN001
            class _Run:
                plan_path = str(tmp_path / ".ralphite" / "plans" / "starter.yaml")
                metadata = {"run_metrics": {"failure_reason_counts": {}}}
                artifacts = []
                status = "paused"

            return _Run()

    monkeypatch.setattr(
        recover_mod, "_orchestrator", lambda _workspace: _FakeOrchestrator()
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "recover",
            "--workspace",
            str(tmp_path),
            "--json",
            "--no-resume",
        ],
    )
    assert result.exit_code == RECOVER_EXIT_PENDING
    payload = json.loads(result.stdout)
    assert payload["status"] == "paused"
    assert payload["data"]["git_warning"].startswith(
        "Workspace has uncommitted changes."
    )
    assert all(item.get("code") != "git.required" for item in payload["issues"])


def test_replay_allows_dirty_workspace_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeOrchestrator:
        def git_repository_status(self) -> dict[str, object]:
            return {
                "ok": True,
                "detail": "git worktree detected (base branch: main)",
                "dirty": True,
            }

        def git_runtime_status(self) -> dict[str, object]:
            return {
                "ok": False,
                "detail": "worktree is dirty in a blocking way",
                "dirty": True,
                "remediation": 'git add -A && git commit -m "save state"',
            }

        def rerun_failed(self, run_id: str) -> str:
            assert run_id == "old-run"
            return "new-run"

        def wait_for_run(self, run_id: str, timeout: float) -> bool:
            return True

        def get_run(self, run_id: str):  # noqa: ANN001
            class _Run:
                status = "succeeded"
                artifacts = []

            return _Run()

    monkeypatch.setattr(
        replay_mod, "_orchestrator", lambda _workspace: _FakeOrchestrator()
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "replay",
            "old-run",
            "--workspace",
            str(tmp_path),
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["data"]["git_warning"].startswith(
        "Workspace has uncommitted changes."
    )


def test_replay_reports_start_preflight_block(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FakeOrchestrator:
        def git_repository_status(self) -> dict[str, object]:
            return {"ok": True, "detail": "git worktree detected", "dirty": False}

        def git_runtime_status(self) -> dict[str, object]:
            return {"ok": True, "detail": "git worktree detected", "dirty": False}

        def rerun_failed(self, run_id: str) -> str:
            raise RunStartBlockedError(
                {
                    "ok": False,
                    "reason": "stale_recovery_state_present",
                    "detail": "workspace has unresolved recoverable runs or stale managed artifacts",
                    "next_commands": [
                        "uv run ralphite recover --workspace . --output table"
                    ],
                }
            )

    monkeypatch.setattr(
        replay_mod, "_orchestrator", lambda _workspace: _FakeOrchestrator()
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["replay", "run-123", "--workspace", str(tmp_path), "--output", "json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert (
        payload["data"]["run_start_preflight"]["reason"]
        == "stale_recovery_state_present"
    )


def test_validate_command_returns_fixes_for_invalid_plan(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    broken = plans / "broken.yaml"
    broken.write_text(_broken_v1_missing_worker("broken"), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(broken),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == "cli-output.v1"
    assert payload["command"] == "validate"
    assert "fixes" in payload["data"]
    assert any(
        item.get("code") == "fix.add_default_worker"
        for item in payload["data"]["fixes"]
    )


def test_validate_apply_safe_fixes_writes_revision(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    broken = plans / "broken2.yaml"
    broken.write_text(_broken_v1_missing_worker("broken2"), encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(broken),
            "--apply-safe-fixes",
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    fixed = payload["data"].get("fixed_revision")
    assert isinstance(fixed, str)
    assert Path(fixed).exists()


def test_validate_json_includes_resolved_execution(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    summary = payload["data"]["summary"]
    resolved = summary.get("resolved_execution", {})
    assert isinstance(resolved, dict)
    assert "template" in resolved
    assert isinstance(resolved.get("resolved_cells"), list)
    assert isinstance(resolved.get("resolved_nodes"), list)


def test_validate_non_v1_returns_version_invalid(tmp_path: Path) -> None:
    plans = tmp_path / ".ralphite" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    invalid_plan = plans / "invalid_v4.yaml"
    invalid_plan.write_text(
        """
version: 4
plan_id: invalid_plan
name: invalid_plan
tasks: []
""",
        encoding="utf-8",
    )
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "validate",
            "--workspace",
            str(tmp_path),
            "--plan",
            str(invalid_plan),
            "--json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert any(
        item.get("code") == "version.invalid" for item in payload.get("issues", [])
    )


def test_quickstart_table_output_shows_run_id_and_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "table",
        ],
    )
    assert result.exit_code in {0, 1}
    assert "Quickstart" in result.stdout
    assert "Capability scope:" in result.stdout
    assert "Quickstart Flow" in result.stdout
    assert "Starting execution..." in result.stdout
    assert "['tool:*']" not in result.stdout


def test_run_table_output_shows_run_id_and_artifacts(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["run", "--workspace", str(tmp_path), "--yes", "--output", "table"]
    )
    assert result.exit_code == 0
    assert "Run ID:" in result.stdout


def test_run_table_output_prints_watch_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "repo with spaces"
    workspace.mkdir()

    class _FakeConfig:
        default_backend = "codex"
        default_model = "gpt-5.3-codex"
        default_reasoning_effort = "medium"

    class _FakeRun:
        status = "succeeded"
        artifacts = []
        metadata = {"run_metrics": {"total_seconds": 0.1}}

    class _FakeOrchestrator:
        config = _FakeConfig()

        def git_runtime_status(self) -> dict[str, object]:
            return {"ok": True}

        def collect_requirements(self, plan_ref=None):  # noqa: ANN001
            return {"tools": [], "mcps": []}

        def start_run(self, **kwargs):  # noqa: ANN003
            return "run-123"

        def wait_for_run(self, run_id: str, timeout: float) -> bool:
            return True

        def get_run(self, run_id: str):  # noqa: ANN001
            return _FakeRun()

    monkeypatch.setattr(
        run_mod, "_orchestrator", lambda _workspace: _FakeOrchestrator()
    )
    monkeypatch.setattr(
        run_mod, "_resolve_plan_ref", lambda orch, plan: workspace / "plan.yaml"
    )
    runner = CliRunner()
    result = runner.invoke(
        app, ["run", "--workspace", str(workspace), "--yes", "--output", "table"]
    )
    assert result.exit_code == 0
    assert "Watch this run:" in result.stdout
    assert "--workspace " in result.stdout
    assert "--run-id run-123" in result.stdout


def test_run_stream_output_surfaces_final_report_preview(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app, ["run", "--workspace", str(tmp_path), "--yes", "--output", "stream"]
    )
    assert result.exit_code == 0
    assert "Final Report:" in result.stdout
    assert "## Outcome" in result.stdout
    assert "final_report.md" in result.stdout


def test_watch_uses_latest_run_when_no_id_passed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    watched: list[tuple[str, bool]] = []

    class _Run:
        id = "latest-run"

    class _FakeOrchestrator:
        def list_history(self, limit=20, query=None):  # noqa: ANN001
            return [_Run()]

        def get_run(self, run_id: str):  # noqa: ANN001
            return _Run()

    monkeypatch.setattr(
        watch_mod, "_orchestrator", lambda _workspace: _FakeOrchestrator()
    )
    monkeypatch.setattr(
        watch_mod,
        "_print_run_stream",
        lambda orch, run_id, verbose=False: watched.append((run_id, verbose)),
    )
    runner = CliRunner()
    result = runner.invoke(app, ["watch", "--workspace", str(tmp_path)])
    assert result.exit_code == 0
    assert "Watching run:" in result.stdout
    assert watched == [("latest-run", False)]


def test_print_run_stream_falls_back_when_console_encoding_breaks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    class _FakeRun:
        artifacts = []

    class _FakeOrchestrator:
        def stream_events(self, run_id: str):  # noqa: ANN001
            yield {
                "event": "RUN_STARTED",
                "level": "info",
                "message": "started",
            }
            yield {
                "event": "RUN_DONE",
                "level": "info",
                "message": "done",
            }

        def wait_for_run(self, run_id: str, timeout: float) -> bool:
            return True

        def get_run(self, run_id: str):  # noqa: ANN001
            return _FakeRun()

    def broken_print(*args, **kwargs):  # noqa: ANN001
        raise UnicodeEncodeError("cp1252", "\u2502", 0, 1, "boom")

    monkeypatch.setattr(core_mod.console, "print", broken_print)
    monkeypatch.setattr(
        core_mod.sys,
        "stdout",
        type(
            "S",
            (),
            {
                "write": lambda self, text: seen.append(text),
                "flush": lambda self: None,
            },
        )(),
    )

    core_mod._print_run_stream(_FakeOrchestrator(), "run-123")
    assert seen


def test_doctor_reports_repository_and_execution_separately_for_dirty_repo(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("dirty change\n", encoding="utf-8")
    result = subprocess.run(
        [
            "uv",
            "run",
            "ralphite",
            "doctor",
            "--workspace",
            str(tmp_path),
            "--output",
            "json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    payload = json.loads(result.stdout)
    checks = payload["data"]["checks"]
    assert any(
        item.get("check") == "git-repository" and item.get("status") == "OK"
        for item in checks
        if isinstance(item, dict)
    )
    assert any(
        item.get("check") == "git-execution" and item.get("status") == "WARN"
        for item in checks
        if isinstance(item, dict)
    )


def test_run_requires_git_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()
    plain = Path(tempfile.mkdtemp())
    runner = CliRunner()
    result = runner.invoke(
        app, ["run", "--workspace", str(plain), "--yes", "--output", "json"]
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert any(item.get("code") == "git.required" for item in payload.get("issues", []))


def test_quickstart_blocks_non_git_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.undo()
    plain = Path(tempfile.mkdtemp())
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(plain),
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    checks = payload.get("data", {}).get("doctor", {}).get("checks", [])
    assert any(
        isinstance(item, dict)
        and item.get("check") == "git-repository"
        and item.get("status") == "FAIL"
        for item in checks
    )
    assert any("git init" in action for action in payload.get("next_actions", []))


def test_quickstart_blocks_dirty_workspace_before_run(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("dirty change\n", encoding="utf-8")
    result = subprocess.run(
        [
            "uv",
            "run",
            "ralphite",
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
        ],
        cwd=Path(__file__).resolve().parents[2],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    checks = payload.get("data", {}).get("doctor", {}).get("checks", [])
    assert any(
        isinstance(item, dict)
        and item.get("check") == "git-execution"
        and item.get("status") == "WARN"
        for item in checks
    )


def test_quickstart_non_strict_allows_noncritical_doctor_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": False,
            "checks": [
                {"check": "recovery-readiness", "status": "FAIL", "detail": "degraded"},
                {"check": "plans", "status": "OK", "detail": "1"},
            ],
            "plan_failures": [],
            "stale_artifacts": {"stale_worktrees": [], "stale_branches": []},
            "fix_suggestions": [],
        }

    monkeypatch.setattr(quickstart_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"


def test_quickstart_strict_doctor_blocks_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [
                {
                    "check": "stale-artifacts",
                    "status": "WARN",
                    "detail": "worktrees=1 branches=0",
                }
            ],
            "plan_failures": [],
            "stale_artifacts": {
                "stale_worktrees": [{"run_id": "x"}],
                "stale_branches": [],
            },
            "fix_suggestions": [],
        }

    monkeypatch.setattr(quickstart_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--yes",
            "--output",
            "json",
            "--strict-doctor",
        ],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(
        item.get("code") == "doctor.failed" for item in payload.get("issues", [])
    )


def test_strict_checks_include_fixture_confidence_suites(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen: list[tuple[list[str], str]] = []
    monkeypatch.setenv("RALPHITE_SKIP_BACKEND_CMD_CHECKS", "1")

    class _Result:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(command, cwd, check, capture_output, text, env):  # noqa: ANN001
        assert "RALPHITE_SKIP_BACKEND_CMD_CHECKS" not in env
        seen.append((list(command), str(cwd)))
        return _Result()

    monkeypatch.setattr(suite_mod.subprocess, "run", fake_run)
    ok, results = suite_mod._run_strict_checks(
        repo_root=Path(__file__).resolve().parents[2],
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert all(
        isinstance(row.get("suite"), str) and row.get("suite") for row in results
    )
    commands = [" ".join(row) for row, _cwd in seen]
    assert any("tests/engine/test_fixture_plan_matrix.py" in row for row in commands)
    assert any(
        "tests/engine/test_dispatched_plan_consistency.py" in row for row in commands
    )
    assert any("tests/engine/test_examples_plans.py" in row for row in commands)
    assert any("tests/cli/test_bootstrap_e2e.py" in row for row in commands)
    assert all(cwd.endswith("Ralphite") for _row, cwd in seen)


def test_check_strict_fails_when_doctor_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": False,
            "checks": [],
            "plan_failures": [],
            "stale_artifacts": {},
            "fix_suggestions": [],
        }

    monkeypatch.setattr(check_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--strict", "--output", "json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(
        item.get("code") == "check.strict_doctor_failed"
        for item in payload.get("issues", [])
    )


def test_check_strict_fails_when_doctor_warns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [
                {
                    "check": "recoverable-runs",
                    "status": "WARN",
                    "detail": "1",
                }
            ],
            "plan_failures": [],
            "stale_artifacts": {},
            "fix_suggestions": [],
        }

    monkeypatch.setattr(check_mod, "_doctor_snapshot", fake_snapshot)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--strict", "--output", "json"],
    )
    assert result.exit_code == 1
    payload = json.loads(result.stdout)
    assert payload["status"] == "failed"
    assert any(
        item.get("code") == "check.strict_doctor_failed"
        for item in payload.get("issues", [])
    )


def test_check_strict_runs_backend_and_strict_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake_snapshot(_orch, include_fix_suggestions=False):  # noqa: ANN001
        return {
            "ok": True,
            "checks": [],
            "plan_failures": [],
            "stale_artifacts": {},
            "fix_suggestions": [],
        }

    def fake_backend_smoke(*, orch, repo_root, quiet, machine_mode, verbose):  # noqa: ANN001
        return True, [
            {
                "suite": "backend-codex-smoke",
                "command": "codex exec",
                "cwd": str(repo_root),
                "exit_code": 0,
            }
        ]

    def fake_strict_checks(*, repo_root, quiet, machine_mode, verbose):  # noqa: ANN001
        return True, [
            {
                "suite": "release",
                "command": "pytest -q",
                "cwd": str(repo_root),
                "exit_code": 0,
            }
        ]

    monkeypatch.setattr(check_mod, "_doctor_snapshot", fake_snapshot)
    monkeypatch.setattr(check_mod, "_run_backend_smoke", fake_backend_smoke)
    monkeypatch.setattr(check_mod, "_run_strict_checks", fake_strict_checks)
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["check", "--workspace", str(tmp_path), "--strict", "--output", "json"],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    commands = payload.get("data", {}).get("commands", [])
    assert any(
        row.get("suite") == "backend-codex-smoke"
        for row in commands
        if isinstance(row, dict)
    )


def test_backend_smoke_codex_command_matches_runtime_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch.config.default_backend = "codex"
    orch.config.default_model = "gpt-5.3-codex"
    orch.config.default_reasoning_effort = "medium"
    repo_root = Path(__file__).resolve().parents[2]
    seen: dict[str, list[str]] = {}

    def fake_run(command, cwd, check, capture_output, text):  # noqa: ANN001
        seen["command"] = list(command)
        return subprocess.CompletedProcess(
            command,
            0,
            stdout='{"type":"item.completed","item":{"type":"agent_message","text":"OK"}}\n',
            stderr="",
        )

    monkeypatch.setattr(suite_mod.subprocess, "run", fake_run)
    ok, _results = suite_mod._run_backend_smoke(
        orch=orch,
        repo_root=repo_root,
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert seen["command"] == build_codex_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        reasoning_effort="medium",
        worktree=repo_root,
        sandbox="read-only",
    )


def test_backend_smoke_cursor_command_matches_runtime_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch.config.default_backend = "cursor"
    orch.config.default_model = "gpt-5.3-codex"
    orch.config.cursor_command = "agent"
    repo_root = Path(__file__).resolve().parents[2]
    seen: dict[str, list[str]] = {}

    def fake_run(command, cwd, check, capture_output, text):  # noqa: ANN001
        seen["command"] = list(command)
        return subprocess.CompletedProcess(
            command, 0, stdout='{"text":"OK"}\n', stderr=""
        )

    monkeypatch.setattr(suite_mod.subprocess, "run", fake_run)
    ok, _results = suite_mod._run_backend_smoke(
        orch=orch,
        repo_root=repo_root,
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert seen["command"] == build_cursor_exec_command(
        prompt="Reply with exactly: OK",
        model="gpt-5.3-codex",
        cursor_command="agent",
        force=True,
    )
    assert "--force" in seen["command"]


def test_doctor_accepts_cursor_powershell_script_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch.config.default_backend = "cursor"
    orch.config.cursor_command = (
        r"C:\Users\alberto.mussali\AppData\Local\cursor-agent\agent.ps1"
    )
    monkeypatch.setattr(
        doctor_mod,
        "probe_cursor_command",
        lambda command: (True, f"powershell.exe -> {command}"),
    )
    snapshot = doctor_mod._doctor_snapshot(orch)
    assert any(
        isinstance(item, dict)
        and item.get("check") == f"cmd:{orch.config.cursor_command}"
        and item.get("status") == "OK"
        for item in snapshot.get("checks", [])
    )


def test_doctor_accepts_codex_powershell_script_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    orch.config.default_backend = "codex"
    monkeypatch.setattr(
        doctor_mod,
        "probe_codex_command",
        lambda: (
            True,
            "powershell.exe -> C:\\Users\\alberto.mussali\\AppData\\Local\\Programs\\codex\\codex.ps1",
        ),
    )
    snapshot = doctor_mod._doctor_snapshot(orch)
    assert any(
        isinstance(item, dict)
        and item.get("check") == "cmd:codex"
        and item.get("status") == "OK"
        for item in snapshot.get("checks", [])
    )


def test_doctor_accepts_python_fallback_on_windows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)

    def fake_which(name):  # noqa: ANN001
        return {
            "python": r"C:\Python313\python.exe",
            "uv": r"C:\Tools\uv.exe",
            "git": r"C:\Tools\git.exe",
            "rg": r"C:\Tools\rg.exe",
        }.get(name)

    monkeypatch.setattr(doctor_mod.shutil, "which", fake_which)
    snapshot = doctor_mod._doctor_snapshot(orch)
    assert any(
        isinstance(item, dict)
        and item.get("check") == "cmd:python"
        and item.get("status") == "OK"
        and "python ->" in str(item.get("detail"))
        for item in snapshot.get("checks", [])
    )


def test_backend_smoke_is_skipped_when_env_requests_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    orch = _orchestrator(tmp_path)
    repo_root = Path(__file__).resolve().parents[2]

    def fail_run(*_args, **_kwargs):  # noqa: ANN001
        raise AssertionError(
            "subprocess.run should not be called when backend checks are skipped"
        )

    monkeypatch.setenv("RALPHITE_SKIP_BACKEND_CMD_CHECKS", "1")
    monkeypatch.setattr(suite_mod.subprocess, "run", fail_run)
    ok, results = suite_mod._run_backend_smoke(
        orch=orch,
        repo_root=repo_root,
        quiet=True,
        machine_mode=True,
        verbose=False,
    )
    assert ok is True
    assert results
    assert results[0]["suite"] == "backend-smoke-skipped"
    assert results[0]["exit_code"] == 0


def test_run_json_propagates_backend_overrides(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "run",
            "--workspace",
            str(tmp_path),
            "--backend",
            "cursor",
            "--model",
            "gpt-5.3-codex",
            "--reasoning-effort",
            "high",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["data"]["backend"] == "cursor"
    assert payload["data"]["model"] == "gpt-5.3-codex"
    assert payload["data"]["reasoning_effort"] == "high"


def test_quickstart_json_propagates_backend_overrides(tmp_path: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "quickstart",
            "--workspace",
            str(tmp_path),
            "--backend",
            "cursor",
            "--model",
            "gpt-5.3-codex",
            "--reasoning-effort",
            "high",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["status"] == "succeeded"
    assert payload["data"]["backend"] == "cursor"
    assert payload["data"]["model"] == "gpt-5.3-codex"
    assert payload["data"]["reasoning_effort"] == "high"


def test_salvage_command_reports_retained_work(tmp_path: Path) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "salvage",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "salvage"
    assert payload["run_id"] == run_id
    rows = payload.get("data", {}).get("rows", [])
    assert isinstance(rows, list)
    assert rows
    assert rows[0].get("salvage_class") in {
        "committed_unmerged",
        "dirty_uncommitted",
        "orphan_managed_artifact",
    }


def test_reconcile_command_reports_run_truth(tmp_path: Path) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "reconcile"
    assert payload["run_id"] == run_id
    data = payload.get("data", {})
    assert isinstance(data.get("nodes"), list)
    assert data.get("inventory")


def test_reconcile_command_can_apply_repaired_state(tmp_path: Path) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    orch = _orchestrator(tmp_path)
    assert orch.recover_run(run_id) is True
    run = orch.get_run(run_id)
    assert run is not None
    retained = run.metadata.get("retained_work", [])
    assert isinstance(retained, list) and retained
    node_id = str(retained[0]["node_id"])
    run.nodes[node_id].status = "failed"
    state_manager = orch.state_manager
    handle = orch.active[run_id]  # noqa: SLF001
    state_manager.persist_runtime_state(handle, "paused")

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "reconcile",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--apply",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["applied"] is True
    repaired = _orchestrator(tmp_path).get_run(run_id)
    assert repaired is not None
    assert repaired.nodes[node_id].status in {"queued", "succeeded"}


def test_promote_salvage_command_promotes_retained_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    orch = _orchestrator(tmp_path)
    run = orch.get_run(run_id)
    assert run is not None
    retained = run.metadata.get("retained_work", [])
    assert isinstance(retained, list)
    node_id = str(retained[0]["node_id"])

    def passing_acceptance(self, node, commit_meta, *, timeout_seconds):  # type: ignore[no-untyped-def]
        return True, {
            "commands": [{"command": "promoted acceptance", "exit_code": 0}],
            "required_artifacts": [],
            "rubric": [],
        }

    monkeypatch.setattr(
        LocalOrchestrator,
        "_evaluate_acceptance",
        passing_acceptance,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "promote-salvage",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--node-id",
            node_id,
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "promote-salvage"
    assert payload["run_id"] == run_id
    assert payload["data"]["run_status"] == "succeeded"


def test_promote_salvage_command_commits_dirty_retained_worker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    orch = _orchestrator(tmp_path)
    run = orch.get_run(run_id)
    assert run is not None
    retained = run.metadata.get("retained_work", [])
    assert isinstance(retained, list) and retained
    node_id = str(retained[0]["node_id"])
    worktree_path = Path(str(retained[0]["worktree_path"]))
    (worktree_path / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    retained[0]["commit"] = ""
    retained[0]["committed"] = False
    retained[0]["salvage_class"] = "dirty_uncommitted"
    orch.history.upsert(run)
    if run_id in orch.active:
        orch.state_manager.persist_runtime_state(orch.active[run_id], run.status)

    def passing_acceptance(self, node, commit_meta, *, timeout_seconds):  # type: ignore[no-untyped-def]
        return True, {
            "commands": [{"command": "promoted acceptance", "exit_code": 0}],
            "required_artifacts": [],
            "rubric": [],
        }

    monkeypatch.setattr(
        LocalOrchestrator,
        "_evaluate_acceptance",
        passing_acceptance,
    )

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "promote-salvage",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--node-id",
            node_id,
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["data"]["commit"]


def test_cleanup_command_preserves_retained_work_by_default(tmp_path: Path) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    orch = _orchestrator(tmp_path)
    run = orch.get_run(run_id)
    assert run is not None
    retained = run.metadata.get("retained_work", [])
    assert isinstance(retained, list)
    worktree_path = Path(str(retained[0]["worktree_path"]))

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "cleanup",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "cleanup"
    assert payload.get("data", {}).get("retained_count", 0) >= 1
    assert worktree_path.exists()


def test_cleanup_command_can_discard_preserved_work(tmp_path: Path) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    orch = _orchestrator(tmp_path)
    run = orch.get_run(run_id)
    assert run is not None
    retained = run.metadata.get("retained_work", [])
    assert isinstance(retained, list)
    worktree_path = Path(str(retained[0]["worktree_path"]))

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "cleanup",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--discard-preserved",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    assert not worktree_path.exists()


def test_salvage_command_works_when_run_state_is_missing(tmp_path: Path) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    _delete_run_state_but_keep_git_artifacts(tmp_path, run_id)
    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "salvage",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "salvage"
    assert payload["run_id"] == run_id
    assert payload.get("issues", [])
    assert payload["issues"][0]["code"] == "run.state_missing"
    rows = payload.get("data", {}).get("rows", [])
    assert isinstance(rows, list)
    assert rows


def test_cleanup_command_can_remove_orphaned_artifacts_when_run_state_is_missing(
    tmp_path: Path,
) -> None:
    run_id = _create_retained_failure_run(tmp_path)
    orch = _orchestrator(tmp_path)
    run = orch.get_run(run_id)
    assert run is not None
    retained = run.metadata.get("retained_work", [])
    assert isinstance(retained, list)
    worktree_path = Path(str(retained[0]["worktree_path"]))
    _delete_run_state_but_keep_git_artifacts(tmp_path, run_id)

    runner = CliRunner()
    result = runner.invoke(
        app,
        [
            "cleanup",
            "--workspace",
            str(tmp_path),
            "--run-id",
            run_id,
            "--discard-preserved",
            "--yes",
            "--output",
            "json",
        ],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["command"] == "cleanup"
    assert payload.get("issues", [])
    assert payload["issues"][0]["code"] == "run.state_missing"
    assert not worktree_path.exists()
