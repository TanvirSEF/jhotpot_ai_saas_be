


import logging

from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)


_PLACEHOLDER_SECRETS = {"change-me", "", "secret", "changeme"}
_PLACEHOLDER_OPENAI  = {"", "sk-your-openai-api-key-here", "your-openai-api-key"}
_PLACEHOLDER_META    = {"", "your-meta-app-id", "your-meta-app-secret"}
_PLACEHOLDER_META_VERIFY = {
    "",
    "nexussuite-verify",
    "replace-with-a-private-webhook-verify-token",
}
_POSTGRESQL_ASYNC_PREFIX = "postgresql+asyncpg://"


def validate_configuration() -> None:


    critical: list[str] = []
    warnings: list[str] = []


    if not settings.DATABASE_URL.startswith(_POSTGRESQL_ASYNC_PREFIX):
        critical.append(
            "DATABASE_URL must use PostgreSQL with the asyncpg driver "
            f"({_POSTGRESQL_ASYNC_PREFIX}...). SQLite is not supported because "
            "the application uses JSONB, pgvector, and HNSW indexes."
        )


    if settings.SECRET_KEY.lower() in _PLACEHOLDER_SECRETS:
        critical.append(
            "SECRET_KEY is set to a default/placeholder value. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    elif len(settings.SECRET_KEY) < 32:
        critical.append("SECRET_KEY must be at least 32 characters long.")

    if settings.ALGORITHM != "HS256":
        critical.append("ALGORITHM must be HS256.")
    if not settings.JWT_ISSUER.strip():
        critical.append("JWT_ISSUER cannot be empty.")
    if not settings.JWT_AUDIENCE.strip():
        critical.append("JWT_AUDIENCE cannot be empty.")
    if settings.ACCESS_TOKEN_EXPIRE_MINUTES <= 0:
        critical.append("ACCESS_TOKEN_EXPIRE_MINUTES must be greater than zero.")


    if settings.FERNET_KEY.lower() in _PLACEHOLDER_SECRETS:
        critical.append(
            "FERNET_KEY is set to a default/placeholder value. "
            "Generate one with cryptography.fernet.Fernet.generate_key()."
        )
    else:
        try:
            Fernet(settings.FERNET_KEY.encode())
        except Exception:
            critical.append(
                "FERNET_KEY is not a valid Fernet key "
                "(must be a URL-safe base64-encoded 32-byte key)."
            )


    if settings.OPENAI_API_KEY in _PLACEHOLDER_OPENAI:
        critical.append(
            "OPENAI_API_KEY is not configured. "
            "Required for embedding generation (Phase A1) and AI reply generation (Phase A4). "
            "Get yours at: https://platform.openai.com/api-keys"
        )


    if settings.META_APP_ID in _PLACEHOLDER_META:
        critical.append(
            "META_APP_ID is not configured. "
            "Required for Facebook OAuth (Phase A2) and webhook verification (Phase A3)."
        )

    if settings.META_APP_SECRET in _PLACEHOLDER_META:
        critical.append(
            "META_APP_SECRET is not configured. "
            "Required for HMAC webhook signature verification (Phase A3)."
        )

    if (
        settings.META_VERIFY_TOKEN in _PLACEHOLDER_META_VERIFY
        or len(settings.META_VERIFY_TOKEN) < 16
    ):
        critical.append(
            "META_VERIFY_TOKEN must be a private value of at least 16 characters."
        )


    if settings.BACKEND_CORS_ORIGINS == ["*"]:
        message = (
            "BACKEND_CORS_ORIGINS cannot use '*' outside local development. "
            "Configure the exact frontend origin instead."
        )
        if settings.ENVIRONMENT in {"staging", "production"}:
            critical.append(message)
        else:
            warnings.append(message)

    if settings.ENVIRONMENT in {"staging", "production"}:
        if not settings.BACKEND_URL.startswith("https://"):
            critical.append("BACKEND_URL must use HTTPS in staging and production.")
        if not settings.FRONTEND_URL.startswith("https://"):
            critical.append("FRONTEND_URL must use HTTPS in staging and production.")

    rate_limit_values = {
        "AUTH_RATE_LIMIT_IP_REQUESTS": settings.AUTH_RATE_LIMIT_IP_REQUESTS,
        "AUTH_RATE_LIMIT_ACCOUNT_REQUESTS": (
            settings.AUTH_RATE_LIMIT_ACCOUNT_REQUESTS
        ),
        "AUTH_RATE_LIMIT_WINDOW_SECONDS": settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
    }
    for name, value in rate_limit_values.items():
        if value <= 0:
            critical.append(f"{name} must be greater than zero.")


    for warning in warnings:
        logger.warning("[Config Warning] %s", warning)

    if critical:
        error_lines = "\n  - ".join(critical)
        raise RuntimeError(
            f"\n\n{'=' * 60}\n"
            f"  STARTUP ABORTED - Configuration errors detected:\n"
            f"  - {error_lines}\n"
            f"{'=' * 60}\n"
            f"  Fix the above issues in your .env file and restart.\n"
            f"{'=' * 60}\n"
        )

    logger.info("Configuration validation passed (%d check groups)", 6)
