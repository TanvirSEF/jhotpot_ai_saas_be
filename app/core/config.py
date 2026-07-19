"""Application configuration.

Values are read from environment variables / the `.env` file via pydantic-settings.
Every setting has a dev-safe default so the app boots even with an empty `.env`.
"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # ── App ─────────────────────────────────────────────
    PROJECT_NAME: str = "NexusSuite API"
    API_V1_STR: str = "/api/v1"
    BACKEND_CORS_ORIGINS: list[str] = ["*"]

    # ── Database ────────────────────────────────────────
    # Defaults to a local SQLite file so the app runs without setup.
    # For PostgreSQL, set in .env, e.g.:
    #   DATABASE_URL=postgresql+psycopg2://user:password@localhost:5432/nexussuite
    DATABASE_URL: str = "sqlite:///./nexussuite.db"

    # ── Security / JWT ──────────────────────────────────
    # IMPORTANT: change SECRET_KEY in production.
    # Generate one with:  python -c "import secrets; print(secrets.token_hex(32))"
    SECRET_KEY: str = "dev-only-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
