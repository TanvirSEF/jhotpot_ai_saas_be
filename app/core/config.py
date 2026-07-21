from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    PROJECT_NAME: str = "NexusSuite API"
    API_V1_STR: str = "/api/v1"
    ENVIRONMENT: Literal["development", "test", "staging", "production"] = (
        "development"
    )
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: Literal["json", "text"] = "json"
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]


    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/nexussuite"
    )

    SECRET_KEY: str = "change-me"
    ALGORITHM: Literal["HS256"] = "HS256"
    JWT_ISSUER: str = "nexussuite-api"
    JWT_AUDIENCE: str = "nexussuite-clients"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440


    REDIS_URL: str = "redis://localhost:6379/0"
    AUTH_RATE_LIMIT_IP_REQUESTS: int = 30
    AUTH_RATE_LIMIT_ACCOUNT_REQUESTS: int = 10
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 60


    FERNET_KEY: str = "change-me"


    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    RAG_MIN_SIMILARITY: float = Field(default=0.55, ge=0.0, le=1.0)
    RAG_MAX_INPUT_CHARS: int = Field(default=2000, ge=200, le=10000)
    RAG_MAX_CONTEXT_CHARS: int = Field(default=6000, ge=1000, le=20000)


    RESUME_EXPORT_STORAGE_PATH: Path = Path("storage/resume_exports")
    RESUME_EXPORT_RECOVERY_SECONDS: int = Field(default=180, ge=60, le=3600)


    HEALTHCHECK_TIMEOUT_SECONDS: float = Field(default=2.0, ge=0.1, le=10.0)
    METRICS_ENABLED: bool = True


    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_VERIFY_TOKEN: str = "nexussuite-verify"


    BACKEND_URL: str = "http://localhost:8000"


    FRONTEND_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
