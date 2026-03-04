from __future__ import annotations

import json
from pathlib import Path


class RunnerStateStore:
    def __init__(self, workspace_root: str) -> None:
        self.path = Path(workspace_root) / ".ralphite" / "runner-state.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def save(self, data: dict) -> None:
        self.path.write_text(json.dumps(data, indent=2), encoding="utf-8")
