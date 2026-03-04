from fastapi.testclient import TestClient

from ralphite_api.db.base import Base
from ralphite_api.db.session import engine
from ralphite_api.main import create_app


def test_bootstrap_creates_default_project_and_lists_runner() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    app = create_app()
    client = TestClient(app)

    signup = client.post("/api/v1/auth/signup", json={"email": "boot@example.com", "password": "secret123"})
    signup.raise_for_status()
    token = signup.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    register = client.post(
        "/api/v1/runner/register",
        json={
            "runner_id": "runner-bootstrap",
            "runner_version": "0.1.0",
            "workspace_root": "/tmp/bootstrap",
            "seeded_starter": True,
            "tools": ["python"],
            "mcp_servers": [],
            "provider_caps": [{"provider": "openai", "models": ["gpt-4.1-mini"]}],
            "plan_files": [],
        },
    )
    register.raise_for_status()

    boot = client.get("/api/v1/bootstrap", headers=headers)
    boot.raise_for_status()
    payload = boot.json()

    assert payload["default_project_id"]
    assert payload["workspace_status"]["status"] in {"not_connected", "pending", "connected"}
    assert any(row["runner_id"] == "runner-bootstrap" for row in payload["runner_candidates"])
