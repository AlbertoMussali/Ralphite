from __future__ import annotations

import os
import pathlib
import sys
import tempfile
import unittest
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fastapi.testclient import TestClient

from app.db import get_sessionmaker, reset_engine_cache
from app.main import create_app
from app.models import Entry, User
from app.settings import get_settings


class EntryCreateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_database_url = os.environ.get("DATABASE_URL")
        self.original_entry_auth_token = os.environ.get("ENTRY_AUTH_TOKEN")

        self.temp_dir = tempfile.TemporaryDirectory()
        os.environ["DATABASE_URL"] = f"sqlite+pysqlite:///{self.temp_dir.name}/entries_create.db"
        os.environ["ENTRY_AUTH_TOKEN"] = "entry-secret-test"

        get_settings.cache_clear()
        reset_engine_cache()

        self.client = TestClient(create_app())
        self.client.get("/health")

    def tearDown(self) -> None:
        self.client.close()

        if self.original_database_url is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = self.original_database_url

        if self.original_entry_auth_token is None:
            os.environ.pop("ENTRY_AUTH_TOKEN", None)
        else:
            os.environ["ENTRY_AUTH_TOKEN"] = self.original_entry_auth_token

        self.temp_dir.cleanup()
        get_settings.cache_clear()
        reset_engine_cache()

    def _create_user(self) -> uuid.UUID:
        session = get_sessionmaker()()
        try:
            user = User(email="creator@example.com", password_hash="not-a-real-hash")
            session.add(user)
            session.commit()
            session.refresh(user)
            return user.id
        finally:
            session.close()

    def test_create_entry_persists_shell_with_initial_status(self) -> None:
        user_id = self._create_user()

        response = self.client.post(
            "/api/v1/entries",
            headers={
                "Authorization": "Bearer entry-secret-test",
                "x-user-id": str(user_id),
            },
        )

        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertIn("entry_id", body)
        self.assertEqual(body["status"], "draft")

        entry_id = uuid.UUID(body["entry_id"])
        session = get_sessionmaker()()
        try:
            entry = session.get(Entry, entry_id)
            self.assertIsNotNone(entry)
            assert entry is not None
            self.assertEqual(entry.user_id, user_id)
            self.assertEqual(entry.status, "draft")
        finally:
            session.close()

    def test_create_entry_requires_existing_user_for_static_token(self) -> None:
        missing_user = uuid.UUID("00000000-0000-0000-0000-000000000000")
        response = self.client.post(
            "/api/v1/entries",
            headers={
                "Authorization": "Bearer entry-secret-test",
                "x-user-id": str(missing_user),
            },
        )
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json(), {"detail": "User not found"})


if __name__ == "__main__":
    unittest.main()
