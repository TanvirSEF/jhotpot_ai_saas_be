from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.core.config import settings

_BCRYPT_MAX = 72  # bcrypt byte limit


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
