from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from pathlib import PurePosixPath

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from ralphite_api.models import PlanFile


def to_datetime(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized)
        except ValueError:
            return None
    return None


def sync_plan_files(db: Session, project_id: str, manifests: list[dict], source: str = "autodiscovered") -> None:
    existing_rows = db.scalars(
        select(PlanFile).where(and_(PlanFile.project_id == project_id, PlanFile.source == source))
    ).all()
    by_path = {row.path: row for row in existing_rows}

    for item in manifests:
        path = item["path"]
        digest = item.get("checksum_sha256") or sha256(path.encode("utf-8")).hexdigest()
        filename = PurePosixPath(path).name
        modified_at = to_datetime(item.get("modified_at"))

        row = by_path.get(path)
        if not row:
            row = PlanFile(
                project_id=project_id,
                source=source,
                origin=source,
                path=path,
                filename=filename,
                checksum_sha256=digest,
                modified_at=modified_at,
                content=item.get("content"),
            )
            db.add(row)
        else:
            row.filename = filename
            row.checksum_sha256 = digest
            row.modified_at = modified_at
            row.origin = source
            if item.get("content"):
                row.content = item.get("content")
            row.updated_at = datetime.now(UTC)
