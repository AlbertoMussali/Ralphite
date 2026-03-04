from fastapi.testclient import TestClient

from ralphite_api.db.base import Base
from ralphite_api.db.session import engine
from ralphite_api.main import create_app


def test_run_events_requires_auth() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    app = create_app()
    client = TestClient(app)

    response = client.get("/api/v1/projects/p1/runs/r1/events")
    assert response.status_code == 401
