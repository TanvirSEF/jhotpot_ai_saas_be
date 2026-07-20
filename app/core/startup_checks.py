"""
Startup configuration validator — Phase A5

Runs at application boot (inside lifespan) BEFORE the server starts
accepting traffic. Any critical misconfiguration immediately raises
RuntimeError so the process exits with a clear error message rather than
failing silently at runtime.

Check categories:
  CRITICAL — app cannot function; raises RuntimeError → process exits
  WARNING  — degraded functionality; logs a warning but continues

Checks performed:
  1. PostgreSQL is configured with the asyncpg driver
  2. SECRET_KEY is non-placeholder and sufficiently long
  3. FERNET_KEY is a syntactically valid Fernet key
  4. OPENAI_API_KEY is present and non-placeholder
  5. META_APP_ID and META_APP_SECRET are present
  6. Production URLs and CORS policy are safe
"""

import logging

from cryptography.fernet import Fernet

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Placeholder sentinels ──────────────────────────────────────────────────────
_PLACEHOLDER_SECRETS = {"change-me", "", "secret", "changeme"}
_PLACEHOLDER_OPENAI  = {"", "sk-your-openai-api-key-here", "your-openai-api-key"}
_PLACEHOLDER_META    = {"", "your-meta-app-id", "your-meta-app-secret"}
_POSTGRESQL_ASYNC_PREFIX = "postgresql+asyncpg://"


def validate_configuration() -> None:
    """
    Validate all critical application settings.

    Call this inside the FastAPI lifespan context manager so the process
    refuses to start with an actionable error message rather than crashing
    later with a cryptic exception.

    Raises:
        RuntimeError: If any CRITICAL check fails. Message lists all issues
                      so the operator can fix them all at once.
    """
    critical: list[str] = []
    warnings: list[str] = []

    # ── 1. Database ───────────────────────────────────────────────────────────
    if not settings.DATABASE_URL.startswith(_POSTGRESQL_ASYNC_PREFIX):
        critical.append(
            "DATABASE_URL must use PostgreSQL with the asyncpg driver "
            f"({_POSTGRESQL_ASYNC_PREFIX}...). SQLite is not supported because "
            "the application uses JSONB, pgvector, and HNSW indexes."
        )

    # ── 2. SECRET_KEY ─────────────────────────────────────────────────────────
    if settings.SECRET_KEY.lower() in _PLACEHOLDER_SECRETS:
        critical.append(
            "SECRET_KEY is set to a default/placeholder value. "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    elif len(settings.SECRET_KEY) < 32:
        critical.append("SECRET_KEY must be at least 32 characters long.")

    # ── 3. FERNET_KEY ─────────────────────────────────────────────────────────
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

    # ── 4. OPENAI_API_KEY ─────────────────────────────────────────────────────
    if settings.OPENAI_API_KEY in _PLACEHOLDER_OPENAI:
        critical.append(
            "OPENAI_API_KEY is not configured. "
            "Required for embedding generation (Phase A1) and AI reply generation (Phase A4). "
            "Get yours at: https://platform.openai.com/api-keys"
        )

    # ── 5. Meta App credentials ───────────────────────────────────────────────
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

    # ── 6. Environment-specific URL and CORS policy ───────────────────────────
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

    # ── Report ────────────────────────────────────────────────────────────────
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
