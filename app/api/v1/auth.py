import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    DUMMY_PASSWORD_HASH,
    create_access_token,
    hash_password,
    validate_password,
    verify_password,
)
from app.core.security_store import (
    AuthRateLimitExceeded,
    SecurityStoreUnavailable,
    enforce_auth_rate_limit,
)
from app.db.session import get_db
from app.models import User

router = APIRouter(prefix="/auth", tags=["auth"])


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str | None = Field(None, max_length=255)
    password: str

    @field_validator("password")
    @classmethod
    def password_is_safe_for_bcrypt(cls, value: str) -> str:
        try:
            validate_password(value)
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
        return value


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


async def _enforce_auth_limit(request: Request, action: str, email: str) -> None:
    client_ip = request.client.host if request.client else None
    try:
        await enforce_auth_rate_limit(
            action=action,
            client_ip=client_ip,
            account_identifier=email,
        )
    except AuthRateLimitExceeded as exc:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many authentication attempts. Please try again later.",
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    except SecurityStoreUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Authentication service is temporarily unavailable.",
        ) from exc


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def register(
    body: UserCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    email = str(body.email).strip().lower()


    await _enforce_auth_limit(request, "register", email)
    exists = await db.scalar(select(User).where(User.email == email))
    if exists:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered")

    user = User(
        email=email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
    )
    db.add(user)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, "Email already registered"
        ) from exc
    await db.refresh(user)
    return user


@router.post("/login", response_model=Token)
async def login(
    body: LoginIn,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    email = str(body.email).strip().lower()
    await _enforce_auth_limit(request, "login", email)
    user = await db.scalar(select(User).where(User.email == email))
    password_hash = user.hashed_password if user else DUMMY_PASSWORD_HASH
    password_is_valid = verify_password(body.password, password_hash)
    if not user or not password_is_valid:
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "User account is inactive")
    return Token(access_token=create_access_token(str(user.id)))
