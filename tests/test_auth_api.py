import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException, Request
from sqlalchemy.exc import IntegrityError

from app.api.v1.auth import LoginIn, UserCreate, login, register


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/auth/login",
            "headers": [],
            "client": ("203.0.113.30", 50000),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


class AuthApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_unknown_email_uses_dummy_password_work(self):
        db = SimpleNamespace(scalar=AsyncMock(return_value=None))
        body = LoginIn(email="missing@example.com", password="wrong-password")

        with (
            patch("app.api.v1.auth._enforce_auth_limit", new=AsyncMock()),
            patch("app.api.v1.auth.verify_password", return_value=False) as verify,
        ):
            with self.assertRaises(HTTPException) as raised:
                await login(body, _request(), db)

        self.assertEqual(raised.exception.status_code, 401)
        verify.assert_called_once()
        self.assertNotEqual(verify.call_args.args[1], "")

    async def test_inactive_user_cannot_receive_token(self):
        user = SimpleNamespace(
            id=uuid.uuid4(),
            hashed_password="stored-hash",
            is_active=False,
        )
        db = SimpleNamespace(scalar=AsyncMock(return_value=user))
        body = LoginIn(email="inactive@example.com", password="valid-password")

        with (
            patch("app.api.v1.auth._enforce_auth_limit", new=AsyncMock()),
            patch("app.api.v1.auth.verify_password", return_value=True),
        ):
            with self.assertRaises(HTTPException) as raised:
                await login(body, _request(), db)

        self.assertEqual(raised.exception.status_code, 403)

    async def test_registration_uniqueness_race_rolls_back(self):
        db = SimpleNamespace(
            scalar=AsyncMock(return_value=None),
            add=Mock(),
            commit=AsyncMock(
                side_effect=IntegrityError(
                    "INSERT INTO users",
                    {},
                    Exception("duplicate email"),
                )
            ),
            rollback=AsyncMock(),
        )
        body = UserCreate(
            email="Person@Example.com",
            full_name="Person",
            password="long-enough-password",
        )

        with (
            patch("app.api.v1.auth._enforce_auth_limit", new=AsyncMock()),
            patch("app.api.v1.auth.hash_password", return_value="hash"),
        ):
            with self.assertRaises(HTTPException) as raised:
                await register(body, _request(), db)

        self.assertEqual(raised.exception.status_code, 400)
        db.rollback.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
