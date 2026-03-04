from __future__ import annotations

import pathlib
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.jobs import JOB_REGISTRY, enqueue_registered_job, run_stub_job, run_transcription_job


class JobRegistryTests(unittest.TestCase):
    def test_stub_job_is_registered(self) -> None:
        self.assertIn("stub.echo", JOB_REGISTRY)
        self.assertEqual(JOB_REGISTRY["stub.echo"]("hello"), {"status": "ok", "payload": "hello"})

    def test_transcription_job_is_registered(self) -> None:
        self.assertIn("transcription.process", JOB_REGISTRY)
        self.assertEqual(
            JOB_REGISTRY["transcription.process"]("entry-id", "asset-id"),
            {"status": "queued", "entry_id": "entry-id", "audio_asset_id": "asset-id"},
        )

    def test_enqueue_registered_job_uses_default_queue(self) -> None:
        fake_queue = MagicMock()
        fake_job = object()
        fake_queue.enqueue.return_value = fake_job

        with patch("app.jobs.get_default_queue", return_value=fake_queue):
            returned_job = enqueue_registered_job("stub.echo", "from-test")

        fake_queue.enqueue.assert_called_once_with(run_stub_job, "from-test")
        self.assertIs(returned_job, fake_job)

    def test_enqueue_registered_job_uses_default_queue_for_transcription(self) -> None:
        fake_queue = MagicMock()
        fake_job = object()
        fake_queue.enqueue.return_value = fake_job

        with patch("app.jobs.get_default_queue", return_value=fake_queue):
            returned_job = enqueue_registered_job("transcription.process", "entry", "asset")

        fake_queue.enqueue.assert_called_once_with(run_transcription_job, "entry", "asset")
        self.assertIs(returned_job, fake_job)

    def test_enqueue_registered_job_rejects_unknown_key(self) -> None:
        with self.assertRaises(KeyError):
            enqueue_registered_job("unknown.job")


if __name__ == "__main__":
    unittest.main()
