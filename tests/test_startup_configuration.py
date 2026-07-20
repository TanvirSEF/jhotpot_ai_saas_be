import unittest
from unittest.mock import patch

from cryptography.fernet import Fernet

from app.core.config import settings
from app.core.startup_checks import validate_configuration


class StartupConfigurationTests(unittest.TestCase):
    def valid_settings(self) -> dict:
        return {
            "DATABASE_URL": "postgresql+asyncpg://user:pass@db:5432/nexussuite",
            "SECRET_KEY": "a" * 64,
            "FERNET_KEY": Fernet.generate_key().decode(),
            "OPENAI_API_KEY": "configured-test-key",
            "META_APP_ID": "configured-app-id",
            "META_APP_SECRET": "configured-app-secret",
            "META_VERIFY_TOKEN": "configured-private-verify-token",
            "ENVIRONMENT": "test",
            "BACKEND_URL": "http://localhost:8000",
            "FRONTEND_URL": "http://localhost:3000",
            "BACKEND_CORS_ORIGINS": ["http://localhost:3000"],
        }

    def validate_with(self, **overrides) -> None:
        values = self.valid_settings()
        values.update(overrides)
        with patch.multiple(settings, **values):
            validate_configuration()

    def test_valid_test_configuration_passes(self):
        self.validate_with()

    def test_sqlite_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "SQLite is not supported"):
            self.validate_with(DATABASE_URL="sqlite+aiosqlite:///./test.db")

    def test_short_secret_key_is_rejected(self):
        with self.assertRaisesRegex(RuntimeError, "at least 32 characters"):
            self.validate_with(SECRET_KEY="too-short")

    def test_production_requires_https_and_explicit_cors(self):
        with self.assertRaises(RuntimeError) as raised:
            self.validate_with(
                ENVIRONMENT="production",
                BACKEND_URL="http://api.example.com",
                FRONTEND_URL="http://app.example.com",
                BACKEND_CORS_ORIGINS=["*"],
            )

        message = str(raised.exception)
        self.assertIn("cannot use '*'", message)
        self.assertIn("BACKEND_URL must use HTTPS", message)
        self.assertIn("FRONTEND_URL must use HTTPS", message)


if __name__ == "__main__":
    unittest.main()
