from __future__ import annotations

from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine


MIGRATIONS: dict[str, tuple[str, ...]] = {
    "projects": (
        "ALTER TABLE projects ADD COLUMN is_default BOOLEAN DEFAULT 0",
    ),
    "workspace_connections": (
        "ALTER TABLE workspace_connections ADD COLUMN runner_id VARCHAR(64)",
        "ALTER TABLE workspace_connections ADD COLUMN bootstrap_state VARCHAR(32) DEFAULT 'pending'",
    ),
    "plan_files": (
        "ALTER TABLE plan_files ADD COLUMN origin VARCHAR(32) DEFAULT 'autodiscovered'",
        "ALTER TABLE plan_files ADD COLUMN version_label VARCHAR(64)",
    ),
}


def apply_runtime_migrations(engine: Engine) -> None:
    """Small compatibility migrations until Alembic is introduced."""
    inspector = inspect(engine)
    with engine.begin() as conn:
        for table_name, statements in MIGRATIONS.items():
            if table_name not in inspector.get_table_names():
                continue
            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for statement in statements:
                parts = statement.split()
                # Expected format: ALTER TABLE <table> ADD COLUMN <column> ...
                if len(parts) < 6:
                    continue
                column_name = parts[5]
                if column_name in existing_columns:
                    continue
                try:
                    conn.execute(text(statement))
                    existing_columns.add(column_name)
                except Exception:
                    # Best effort for local dev compatibility.
                    continue
