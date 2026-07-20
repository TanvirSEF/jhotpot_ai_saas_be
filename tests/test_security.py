import unittest
from datetime import datetime, timezone

import jwt
from pydantic import ValidationError

from app.api.v1.auth import UserCreate
from app.core.config import settings
from app.core.security import (
    create_access_token,
    decode_token,
    hash_password,
    validate_password,
    verify_password,
)


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


class AccessTokenSecurityTests(unittest.TestCase):
    def test_access_token_contains_and_validates_required_claims(self):
        subject = "2512e669-e49f-48ea-bcc5-87f47090d878"
        token = create_access_token(subject)
        payload = decode_token(token)

        self.assertEqual(payload["sub"], subject)
        self.assertEqual(payload["iss"], settings.JWT_ISSUER)
        self.assertEqual(payload["aud"], settings.JWT_AUDIENCE)
        self.assertEqual(payload["token_type"], "access")
        self.assertIn("jti", payload)
        self.assertIn("iat", payload)
        self.assertIn("nbf", payload)
        self.assertIn("exp", payload)

    def test_token_missing_required_claims_is_rejected(self):
        token = jwt.encode(
            {
                "sub": "2512e669-e49f-48ea-bcc5-87f47090d878",
                "exp": datetime.now(timezone.utc).timestamp() + 60,
            },
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )

        with self.assertRaises(jwt.MissingRequiredClaimError):
            decode_token(token)

    def test_non_access_token_is_rejected(self):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": "2512e669-e49f-48ea-bcc5-87f47090d878",
            "iat": now,
            "nbf": now,
            "exp": now.timestamp() + 60,
            "iss": settings.JWT_ISSUER,
            "aud": settings.JWT_AUDIENCE,
            "jti": "state-id",
            "token_type": "oauth_state",
        }
        token = jwt.encode(
            payload,
            settings.SECRET_KEY,
            algorithm=settings.ALGORITHM,
        )

        with self.assertRaisesRegex(jwt.InvalidTokenError, "not an access token"):
            decode_token(token)


if __name__ == "__main__":
    unittest.main()
