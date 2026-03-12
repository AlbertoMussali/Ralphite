from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class GitRuntimeContext:
    workspace_root: Path
    run_id: str
    base_branch: str
