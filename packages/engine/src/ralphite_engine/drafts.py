from __future__ import annotations

from pathlib import Path

from ralphite_engine.models import PlanDraftState


def save_draft(drafts_dir: Path, draft: PlanDraftState) -> Path:
    drafts_dir.mkdir(parents=True, exist_ok=True)
    path = drafts_dir / f"{draft.id}.yaml"
    path.write_text(draft.content, encoding="utf-8")
    return path


def load_drafts(drafts_dir: Path) -> list[PlanDraftState]:
    drafts: list[PlanDraftState] = []
    if not drafts_dir.exists():
        return drafts
    for path in sorted(drafts_dir.glob("*.yaml"), key=lambda p: p.stat().st_mtime, reverse=True):
        drafts.append(
            PlanDraftState(
                id=path.stem,
                path=str(path),
                title=path.stem,
                content=path.read_text(encoding="utf-8"),
            )
        )
    return drafts
