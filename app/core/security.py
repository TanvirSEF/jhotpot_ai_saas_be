"""Security utilities including password hashing, JWTs, and Fernet token encryption."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)
PASSWORD_MIN_LENGTH = 10
_BCRYPT_MAX_BYTES = 72
# Used only to equalize login work when an email does not exist. It is not a
# credential and does not grant access to any account.
DUMMY_PASSWORD_HASH = (
    "$2b$12$lreigrcUjPDfvQue1Rw8xufZkePYD4tQULEBLi.d2MuxKGGJ8bmhe"
)
_fernet: Fernet | None = None


def _get_fernet() -> Fernet:
    global _fernet
    if _fernet is None:
        try:
            _fernet = Fernet(settings.FERNET_KEY.encode())
        except Exception as e:
            logger.critical("Invalid FERNET_KEY configuration: %s", e)
            raise RuntimeError("Invalid FERNET_KEY") from e
    return _fernet


def hash_password(password: str) -> str:
    validate_password(password)
    raw = password.encode("utf-8")
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    raw = password.encode("utf-8")
    if len(raw) > _BCRYPT_MAX_BYTES:
        return False
    try:
        return bcrypt.checkpw(raw, hashed.encode())
    except ValueError:
        return False


def validate_password(password: str) -> None:
    """Validate a new password without silently changing its value."""
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(
            f"Password must be at least {PASSWORD_MIN_LENGTH} characters long"
        )
    if len(password.encode("utf-8")) > _BCRYPT_MAX_BYTES:
        raise ValueError("Password must not exceed 72 UTF-8 bytes")


def create_access_token(sub: str, expires_in: timedelta | None = None) -> str:
    now = datetime.now(timezone.utc)
    exp = now + (
        expires_in or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {
        "sub": sub,
        "iat": now,
        "nbf": now,
        "exp": exp,
        "iss": settings.JWT_ISSUER,
        "aud": settings.JWT_AUDIENCE,
        "jti": str(uuid.uuid4()),
        "token_type": "access",
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    payload = jwt.decode(
        token,
        settings.SECRET_KEY,
        algorithms=[settings.ALGORITHM],
        audience=settings.JWT_AUDIENCE,
        issuer=settings.JWT_ISSUER,
        options={
            "require": [
                "sub",
                "iat",
                "nbf",
                "exp",
                "iss",
                "aud",
                "jti",
                "token_type",
            ]
        },
    )
    if payload.get("token_type") != "access":
        raise jwt.InvalidTokenError("Token is not an access token")
    return payload


def encrypt_token(plain_text: str) -> str:
    if not plain_text or not plain_text.strip():
        raise ValueError("Token plain text cannot be empty")
    fernet = _get_fernet()
    return fernet.encrypt(plain_text.encode("utf-8")).decode("utf-8")


def decrypt_token(cipher_text: str) -> str:
    if not cipher_text or not cipher_text.strip():
        raise ValueError("Cipher text cannot be empty")
    fernet = _get_fernet()
    try:
        return fernet.decrypt(cipher_text.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        logger.error("Failed to decrypt token")
        raise
