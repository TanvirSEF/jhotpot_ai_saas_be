"""Security utilities including password hashing, JWTs, and Fernet token encryption."""

import logging
from datetime import datetime, timedelta, timezone
import bcrypt
import jwt
from cryptography.fernet import Fernet, InvalidToken
from app.core.config import settings

logger = logging.getLogger(__name__)
_BCRYPT_MAX = 72
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
    raw = password.encode("utf-8")[:_BCRYPT_MAX]
    return bcrypt.hashpw(raw, bcrypt.gensalt()).decode()


def verify_password(password: str, hashed: str) -> bool:
    raw = password.encode("utf-8")[:_BCRYPT_MAX]
    try:
        return bcrypt.checkpw(raw, hashed.encode())
    except ValueError:
        return False


def create_access_token(sub: str, expires_in: timedelta | None = None) -> str:
    exp = datetime.now(timezone.utc) + (
        expires_in or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    payload = {"exp": exp, "sub": sub}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])


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
