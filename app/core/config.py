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
    BACKEND_CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    # NexusSuite relies on PostgreSQL-only features such as JSONB and pgvector.
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@localhost:5432/nexussuite"
    )

    SECRET_KEY: str = "change-me"
    ALGORITHM: Literal["HS256"] = "HS256"
    JWT_ISSUER: str = "nexussuite-api"
    JWT_AUDIENCE: str = "nexussuite-clients"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440

    # Redis (broker + backend for Celery)
    REDIS_URL: str = "redis://localhost:6379/0"
    AUTH_RATE_LIMIT_IP_REQUESTS: int = 30
    AUTH_RATE_LIMIT_ACCOUNT_REQUESTS: int = 10
    AUTH_RATE_LIMIT_WINDOW_SECONDS: int = 60

    # Fernet key for AES encryption of Meta Page Access Tokens (PRD §6.2)
    # Generate with a short Python command using cryptography.fernet.Fernet.
    FERNET_KEY: str = "change-me"

    # ── OpenAI (Phase A1: embeddings & LLM, Phase A4: RAG) ──────────────────
    # text-embedding-3-small  →  1 536 dims, matches pgvector column
    # Chat model              →  gpt-4o or gpt-4o-mini for auto-replies
    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"
    RAG_MIN_SIMILARITY: float = Field(default=0.55, ge=0.0, le=1.0)
    RAG_MAX_INPUT_CHARS: int = Field(default=2000, ge=200, le=10000)
    RAG_MAX_CONTEXT_CHARS: int = Field(default=6000, ge=1000, le=20000)

    # Durable local export storage. Production may replace the adapter with S3.
    RESUME_EXPORT_STORAGE_PATH: Path = Path("storage/resume_exports")
    RESUME_EXPORT_RECOVERY_SECONDS: int = Field(default=180, ge=60, le=3600)

    # ── Meta / Facebook App (Phase A2: OAuth, Phase A3: Webhooks) ───────────
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_VERIFY_TOKEN: str = "nexussuite-verify"

    # ── URL config (swap per environment) ───────────────────────────────────
    # Dev:  BACKEND_URL  = ngrok https URL  (e.g. https://xxxx.ngrok-free.app)
    # Prod: BACKEND_URL  = https://api.yourdomain.com
    BACKEND_URL: str = "http://localhost:8000"
    # Dev:  FRONTEND_URL = http://localhost:3000
    # Prod: FRONTEND_URL = https://yourdomain.com
    FRONTEND_URL: str = "http://localhost:3000"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


settings = Settings()
