from __future__ import annotations

import json
from pathlib import Path

from ralphite.engine.models import HistoryIndex, RunViewState


class HistoryStore:
    def __init__(self, history_path: Path) -> None:
        self.history_path = history_path
        self.history_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> HistoryIndex:
        if not self.history_path.exists():
            return HistoryIndex()
        try:
            data = json.loads(self.history_path.read_text(encoding="utf-8"))
            return HistoryIndex.model_validate(data)
        except Exception:  # noqa: BLE001
            return HistoryIndex()

    def save(self, index: HistoryIndex) -> None:
        self.history_path.write_text(index.model_dump_json(indent=2), encoding="utf-8")

    def upsert(self, run: RunViewState) -> None:
        index = self.load()
        replaced = False
        for idx, row in enumerate(index.runs):
            if row.id == run.id:
                index.runs[idx] = run
                replaced = True
                break
        if not replaced:
            index.runs.insert(0, run)
        self.save(index)

    def get(self, run_id: str) -> RunViewState | None:
        index = self.load()
        for row in index.runs:
            if row.id == run_id:
                return row
        return None

    def list(self, limit: int = 20, query: str | None = None) -> list[RunViewState]:
        rows = self.load().runs
        if query:
            needle = query.lower()
            rows = [
                row
                for row in rows
                if needle in row.id.lower()
                or needle in row.status.lower()
                or needle in row.plan_path.lower()
            ]
        return rows[: max(1, limit)]
