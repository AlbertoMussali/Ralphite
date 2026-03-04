from pathlib import Path

from fastapi.testclient import TestClient

from ralphite_api.db.base import Base
from ralphite_api.db.session import engine
from ralphite_api.main import create_app
from ralphite_api.services.plan_templates import workspace_relative_path


def test_connect_runner_and_save_versioned_plan(tmp_path: Path) -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    app = create_app()
    client = TestClient(app)

    signup = client.post("/api/v1/auth/signup", json={"email": "builder@example.com", "password": "secret123"})
    signup.raise_for_status()
    user_headers = {"Authorization": f"Bearer {signup.json()['access_token']}"}

    workspace_root = str(tmp_path)
    register = client.post(
        "/api/v1/runner/register",
        json={
            "runner_id": "runner-builder",
            "runner_version": "0.1.0",
            "workspace_root": workspace_root,
            "seeded_starter": True,
            "tools": ["python"],
            "mcp_servers": [],
            "provider_caps": [{"provider": "openai", "models": ["gpt-4.1-mini"]}],
            "plan_files": [],
        },
    )
    register.raise_for_status()

    bootstrap = client.get("/api/v1/bootstrap", headers=user_headers)
    bootstrap.raise_for_status()
    project_id = bootstrap.json()["default_project_id"]

    connect = client.post(
        f"/api/v1/projects/{project_id}/workspace/connect-runner",
        json={"runner_id": "runner-builder"},
        headers=user_headers,
    )
    connect.raise_for_status()
    assert connect.json()["status"] == "connected"

    plan_dict = {
        "version": 1,
        "plan_id": "test_builder",
        "name": "Builder Test",
        "agents": [{"id": "worker", "provider": "openai", "model": "gpt-4.1-mini"}],
        "graph": {
            "nodes": [
                {"id": "n1", "kind": "agent", "agent_id": "worker", "task": "do work", "depends_on": []}
            ]
        },
    }
    saved = client.post(
        f"/api/v1/projects/{project_id}/plans/save-versioned",
        json={"plan": plan_dict, "filename_hint": "builder-test"},
        headers=user_headers,
    )
    saved.raise_for_status()
    saved_payload = saved.json()
    assert saved_payload["source"] == "builder"
    assert saved_payload["path"].startswith(".ralphite/plans/")

    discovered = client.get(f"/api/v1/projects/{project_id}/plans/discovered", headers=user_headers)
    discovered.raise_for_status()
    assert any(item["id"] == saved_payload["id"] for item in discovered.json())


def test_workspace_relative_path_handles_alias_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    plans_dir = workspace / ".ralphite" / "plans"
    plans_dir.mkdir(parents=True)
    target = plans_dir / "builder.test.yaml"
    target.write_text("version: 1\n", encoding="utf-8")

    alias_root = tmp_path / "alias_workspace"
    alias_root.symlink_to(workspace, target_is_directory=True)

    relative = workspace_relative_path(str(alias_root), target)
    assert relative == ".ralphite/plans/builder.test.yaml"
