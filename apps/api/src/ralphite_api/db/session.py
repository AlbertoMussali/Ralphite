from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ralphite_api.core.config import settings


def _normalize_sqlite_path(url: str) -> str:
    if not url.startswith("sqlite"):
        return url
    if "///./" in url:
        relative = url.split("///./", 1)[1]
        Path(relative).parent.mkdir(parents=True, exist_ok=True)
    return url


engine = create_engine(_normalize_sqlite_path(settings.database_url), echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
