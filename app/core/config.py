from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "NexusSuite API"
    API_V1_STR: str = "/api/v1"
    BACKEND_CORS_ORIGINS: list[str] = ["*"]

    # sqlite+aiosqlite (dev) or postgresql+asyncpg://... (prod)
    DATABASE_URL: str = "sqlite+aiosqlite:///./nexussuite.db"

    SECRET_KEY: str = "change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Redis (broker + backend for Celery)
    REDIS_URL: str = "redis://localhost:6379/0"

    # Fernet key for AES encryption of Meta Page Access Tokens (PRD 6.2)
    # Must be a URL-safe base64-encoded 32-byte key.
    FERNET_KEY: str = "change-me"

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=True, extra="ignore"
    )


settings = Settings()
