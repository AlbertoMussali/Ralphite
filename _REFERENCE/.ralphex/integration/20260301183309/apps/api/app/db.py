from __future__ import annotations

from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.settings import get_database_url


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for Alembic metadata discovery."""


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return create_engine(get_database_url(), pool_pre_ping=True)


def reset_engine_cache() -> None:
    get_engine.cache_clear()


def get_sessionmaker() -> sessionmaker:
    return sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, expire_on_commit=False)


def initialize_schema() -> None:
    """Create tables from SQLAlchemy metadata (SQLite/testing convenience)."""
    Base.metadata.create_all(bind=get_engine())


def check_db_connection() -> bool:
    """Lightweight connectivity probe for health checks and smoke tests."""
    with get_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
    return True


def get_db() -> Session:
    """Yield a SQLAlchemy session for request-scoped dependencies."""
    db = get_sessionmaker()()
    try:
        yield db
    finally:
        db.close()

