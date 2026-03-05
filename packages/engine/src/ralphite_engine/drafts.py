from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from ralphite_engine.models import PlanDraftState


def _now_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def save_draft(drafts_dir: Path, draft: PlanDraftState) -> Path:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{draft.id}.yaml"
    path.write_text(draft.content, encoding="utf-8")
    return path


def autosave_snapshot(drafts_dir: Path, *, draft_id: str, content: str) -> Path:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{draft_id}.{_now_compact()}.autosave.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def latest_snapshot(drafts_dir: Path, *, draft_id: str | None = None) -> Path | None:
    if not drafts_dir.exists():
        return None
    pattern = f"{draft_id}.*.autosave.yaml" if draft_id else "*.autosave.yaml"
    snapshots = sorted(
        drafts_dir.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True
    )
    return snapshots[0] if snapshots else None


def load_drafts(drafts_dir: Path) -> list[PlanDraftState]:
    drafts: list[PlanDraftState] = []
    if not drafts_dir.exists():
        return drafts
    for path in sorted(
        drafts_dir.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True
    ):
        if path.name.endswith(".autosave.yaml"):
            continue
        drafts.append(
            PlanDraftState(
                id=path.stem,
                path=str(path),
                title=path.stem,
                content=path.read_text(encoding="utf-8"),
            )
        )
    return drafts
