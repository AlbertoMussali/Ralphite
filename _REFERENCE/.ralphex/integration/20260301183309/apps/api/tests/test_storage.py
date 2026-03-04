from __future__ import annotations

import os
import pathlib
import shutil
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.settings import get_settings
from app.storage import get_storage_backend
from app.storage.base import StorageNotFoundError


class LocalDiskStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.mkdtemp(prefix="voicevault-storage-test-")
        self.original_storage_backend = os.environ.get("STORAGE_BACKEND")
        self.original_storage_root = os.environ.get("STORAGE_LOCAL_ROOT")
        os.environ["STORAGE_BACKEND"] = "local"
        os.environ["STORAGE_LOCAL_ROOT"] = self.temp_dir
        get_settings.cache_clear()
        get_storage_backend.cache_clear()

    def tearDown(self) -> None:
        if self.original_storage_backend is None:
            os.environ.pop("STORAGE_BACKEND", None)
        else:
            os.environ["STORAGE_BACKEND"] = self.original_storage_backend

        if self.original_storage_root is None:
            os.environ.pop("STORAGE_LOCAL_ROOT", None)
        else:
            os.environ["STORAGE_LOCAL_ROOT"] = self.original_storage_root

        get_settings.cache_clear()
        get_storage_backend.cache_clear()
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_put_and_get_by_storage_key(self) -> None:
        storage = get_storage_backend()
        key = "audio/user-1/entry-1.webm"
        payload = b"fake audio bytes"

        returned_key = storage.put(key, payload)
        loaded = storage.get(key)

        self.assertEqual(returned_key, key)
        self.assertEqual(loaded, payload)

    def test_delete_removes_storage_key(self) -> None:
        storage = get_storage_backend()
        key = "audio/user-2/entry-2.webm"
        storage.put(key, b"content")

        storage.delete(key)

        with self.assertRaises(StorageNotFoundError):
            storage.get(key)

    def test_rejects_path_escape_keys(self) -> None:
        storage = get_storage_backend()

        with self.assertRaises(ValueError):
            storage.put("../outside.webm", b"blocked")


if __name__ == "__main__":
    unittest.main()
