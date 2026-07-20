import asyncio
import os
import unittest
from pathlib import Path
from unittest.mock import patch

import asyncpg
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.core.config import settings


ROOT = Path(__file__).resolve().parents[2]
RUN_DATABASE_TESTS = os.getenv("RUN_DATABASE_INTEGRATION_TESTS") == "1"


def _guard_disposable_database(database_url: str) -> None:
    """Refuse to run destructive migration tests against a shared database."""
    url = make_url(database_url)
    expected_hosts = {"127.0.0.1", "localhost"}

    if (
        url.drivername != "postgresql+asyncpg"
        or url.host not in expected_hosts
        or url.port != 55432
        or url.database != "nexussuite_test"
    ):
        raise RuntimeError(
            "Migration integration tests require the disposable database at "
            "postgresql+asyncpg://...@127.0.0.1:55432/nexussuite_test"
        )


def _asyncpg_dsn(database_url: str) -> str:
    return database_url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def _execute(database_url: str, query: str, *args):
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    try:
        return await connection.execute(query, *args)
    finally:
        await connection.close()


async def _fetchrow(database_url: str, query: str, *args):
    connection = await asyncpg.connect(_asyncpg_dsn(database_url))
    try:
        return await connection.fetchrow(query, *args)
    finally:
        await connection.close()


@unittest.skipUnless(
    RUN_DATABASE_TESTS,
    "Set RUN_DATABASE_INTEGRATION_TESTS=1 to run disposable database tests.",
)
class MigrationIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.database_url = os.environ["TEST_DATABASE_URL"]
        _guard_disposable_database(cls.database_url)
        cls.alembic_config = Config(str(ROOT / "alembic.ini"))

    def test_full_migration_lifecycle_preserves_users_and_has_no_drift(self):
        with patch.object(settings, "DATABASE_URL", self.database_url):
            command.downgrade(self.alembic_config, "base")
            command.upgrade(self.alembic_config, "e79e777a4bcc")

            asyncio.run(
                _execute(
                    self.database_url,
                    """
                    INSERT INTO users (email, full_name, hashed_password)
                    VALUES ($1, $2, $3)
                    """,
                    "migration-test@example.com",
                    "Migration Test User",
                    "not-a-real-password-hash",
                )
            )

            command.upgrade(self.alembic_config, "head")
            upgraded_user = asyncio.run(
                _fetchrow(
                    self.database_url,
                    """
                    SELECT email, full_name, pg_typeof(id)::text AS id_type,
                           is_active
                    FROM users
                    WHERE email = $1
                    """,
                    "migration-test@example.com",
                )
            )

            self.assertIsNotNone(upgraded_user)
            self.assertEqual(upgraded_user["id_type"], "uuid")
            self.assertTrue(upgraded_user["is_active"])

            command.check(self.alembic_config)

            command.downgrade(self.alembic_config, "e79e777a4bcc")
            downgraded_user = asyncio.run(
                _fetchrow(
                    self.database_url,
                    """
                    SELECT email, full_name, pg_typeof(id)::text AS id_type
                    FROM users
                    WHERE email = $1
                    """,
                    "migration-test@example.com",
                )
            )

            self.assertIsNotNone(downgraded_user)
            self.assertEqual(downgraded_user["id_type"], "integer")

            command.upgrade(self.alembic_config, "head")
            command.check(self.alembic_config)


if __name__ == "__main__":
    unittest.main()
