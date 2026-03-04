from fastapi.testclient import TestClient

from ralphite_api.db.base import Base
from ralphite_api.db.session import engine
from ralphite_api.main import create_app


def test_run_lifecycle_end_to_end() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    app = create_app()
    client = TestClient(app)

    signup = client.post("/api/v1/auth/signup", json={"email": "user@example.com", "password": "secret123"})
    signup.raise_for_status()
    token = signup.json()["access_token"]
    user_headers = {"Authorization": f"Bearer {token}"}

    project = client.post("/api/v1/projects", json={"name": "Demo"}, headers=user_headers)
    project.raise_for_status()
    project_id = project.json()["id"]

    connect = client.post(
        f"/api/v1/projects/{project_id}/workspace/connect",
        json={"workspace_root": "/tmp/demo"},
        headers=user_headers,
    )
    connect.raise_for_status()

    plan = """
version: 1
plan_id: sample
name: sample
agents:
  - id: a1
    provider: openai
    model: gpt-4.1-mini
graph:
  nodes:
    - id: n1
      kind: agent
      group: plan
      agent_id: a1
      task: plan
      depends_on: []
"""

    register = client.post(
        "/api/v1/runner/register",
        json={
            "runner_id": "runner-test",
            "runner_version": "0.1.0",
            "workspace_root": "/tmp/demo",
            "tools": ["python"],
            "mcp_servers": [],
            "provider_caps": [{"provider": "openai", "models": ["gpt-4.1-mini"]}],
            "plan_files": [
                {
                    "path": ".ralphite/plans/sample.yaml",
                    "checksum_sha256": "abc123",
                    "content": plan,
                }
            ],
        },
    )
    register.raise_for_status()
    runner_headers = {"X-Runner-Token": register.json()["token"]}

    plans = client.get(f"/api/v1/projects/{project_id}/plans/discovered", headers=user_headers)
    plans.raise_for_status()
    plan_file_id = plans.json()[0]["id"]

    run = client.post(f"/api/v1/projects/{project_id}/runs", json={"plan_file_id": plan_file_id}, headers=user_headers)
    run.raise_for_status()
    run_id = run.json()["id"]

    claim = client.post("/api/v1/runner/claim-next", json={"runner_id": "runner-test"}, headers=runner_headers)
    claim.raise_for_status()
    node_record_id = claim.json()["node_record_id"]

    complete = client.post(
        f"/api/v1/runner/runs/{run_id}/complete",
        json={
            "runner_id": "runner-test",
            "node_record_id": node_record_id,
            "result": {"ok": True},
            "outcome": "success",
        },
        headers=runner_headers,
    )
    complete.raise_for_status()

    details = client.get(f"/api/v1/projects/{project_id}/runs/{run_id}", headers=user_headers)
    details.raise_for_status()
    assert details.json()["status"] == "succeeded"
