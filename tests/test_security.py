import unittest

from pydantic import ValidationError

from app.api.v1.auth import UserCreate
from app.core.security import hash_password, validate_password, verify_password


class PasswordSecurityTests(unittest.TestCase):
    def test_valid_password_hashes_and_verifies(self):
        password = "correct-horse-battery-staple"
        hashed = hash_password(password)

        self.assertTrue(verify_password(password, hashed))
        self.assertFalse(verify_password("different-password", hashed))

    def test_short_password_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least 10 characters"):
            validate_password("short")

    def test_password_over_72_utf8_bytes_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "72 UTF-8 bytes"):
            validate_password("🙂" * 19)

    def test_registration_schema_applies_password_policy(self):
        with self.assertRaises(ValidationError):
            UserCreate(
                email="person@example.com",
                password="short",
            )


if __name__ == "__main__":
    unittest.main()
