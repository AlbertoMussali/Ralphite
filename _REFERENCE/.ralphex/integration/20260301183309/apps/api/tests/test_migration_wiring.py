from __future__ import annotations

import configparser
import pathlib
import unittest


ROOT = pathlib.Path(__file__).resolve().parents[1]


class MigrationWiringTests(unittest.TestCase):
    def test_alembic_ini_defaults_to_compose_postgres(self) -> None:
        parser = configparser.ConfigParser()
        parser.read(ROOT / "alembic.ini")
        self.assertEqual(
            parser["alembic"]["sqlalchemy.url"],
            "postgresql+psycopg://voicevault:voicevault@db:5432/voicevault",
        )

    def test_env_uses_shared_database_url(self) -> None:
        env_py = (ROOT / "alembic" / "env.py").read_text(encoding="utf-8")
        self.assertIn("from app.settings import get_database_url", env_py)
        self.assertIn('config.set_main_option("sqlalchemy.url", get_database_url())', env_py)

    def test_core_migrations_exist(self) -> None:
        versions = ROOT / "alembic" / "versions"
        migration_files = [path.name for path in versions.glob("*.py")]
        self.assertIn("20260302_0002_create_schema_v1.py", migration_files)
        self.assertIn("20260302_0003_create_refresh_sessions.py", migration_files)

    def test_schema_v1_migration_creates_required_tables(self) -> None:
        migration_py = (ROOT / "alembic" / "versions" / "20260302_0002_create_schema_v1.py").read_text(
            encoding="utf-8"
        )
        self.assertIn("op.create_table(", migration_py)
        for table_name in ["users", "entries", "transcripts", "audio_assets", "tags", "audit_log"]:
            self.assertIn(f'"{table_name}"', migration_py)


if __name__ == "__main__":
    unittest.main()

