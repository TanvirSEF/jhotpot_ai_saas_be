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

    # Fernet key for AES encryption of Meta Page Access Tokens (PRD §6.2)
    # Generate: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    FERNET_KEY: str = "change-me"

    # ── OpenAI (Phase A1: embeddings & LLM, Phase A4: RAG) ──────────────────
    # text-embedding-3-small  →  1 536 dims, matches pgvector column
    # Chat model              →  gpt-4o or gpt-4o-mini for auto-replies
    OPENAI_API_KEY: str = ""
    OPENAI_EMBEDDING_MODEL: str = "text-embedding-3-small"
    OPENAI_CHAT_MODEL: str = "gpt-4o-mini"

    # ── Meta / Facebook App (Phase A2: OAuth, Phase A3: Webhooks) ───────────
    META_APP_ID: str = ""
    META_APP_SECRET: str = ""
    META_VERIFY_TOKEN: str = "nexussuite-verify"  # arbitrary secret used in webhook setup

    model_config = SettingsConfigDict(
        env_file=".env", case_sensitive=True, extra="ignore"
    )


settings = Settings()
