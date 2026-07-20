from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, bot, fb, knowledge, resume, org
from app.core.config import settings
from app.core.logging import get_logger
from app.core.startup_checks import validate_configuration
from app.middleware.request_id import RequestIDMiddleware
from app.models import User  # noqa: F401 registers models with SQLAlchemy

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """
    Application lifespan — runs setup before serving, cleanup on shutdown.

    Boot sequence:
      1. Validate critical configuration (fail fast on misconfig)
      2. Run Alembic migrations (idempotent, safe to run on every boot)
      3. Verify Redis connectivity (Celery broker/backend)
    """
    import asyncio
    import redis as redis_client
    from alembic import command
    from alembic.config import Config

    # ── 1. Config validation (fail fast) ──────────────────────────────────────
    # Raises RuntimeError with actionable message if any critical value
    # is still a placeholder or invalid.
    validate_configuration()

    # ── 2. Database migrations ────────────────────────────────────────────────
    def run_migrations():
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_migrations)
    logger.info("Database migrations applied")

    # ── 3. Redis connectivity check ───────────────────────────────────────────
    r = redis_client.from_url(settings.REDIS_URL, socket_connect_timeout=5)
    r.ping()
    r.close()
    logger.info("Redis connection verified")

    logger.info(
        "%s is ready to serve traffic on %s",
        settings.PROJECT_NAME,
        settings.BACKEND_URL,
    )

    yield

    logger.info("Application shutdown complete.")


# ── Application factory ───────────────────────────────────────────────────────

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    lifespan=lifespan,
)

# ── Middleware stack (order matters: outermost = first to process) ─────────────

# 1. Request Correlation ID — must be first so all downstream code has request_id
app.add_middleware(RequestIDMiddleware)

# 2. CORS
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ── Base routes ───────────────────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
def index():
    return {"name": settings.PROJECT_NAME, "version": "1.0.0"}


@app.get("/health", tags=["monitoring"], summary="Deep health check")
async def health() -> dict:
    """
    Deep health check — verifies all critical dependencies are reachable.

    Used by Docker HEALTHCHECK, Railway/Render deploy pipelines, and
    external uptime monitors. Returns HTTP 200 only if ALL checks pass.
    Returns HTTP 503 if any dependency is unreachable.

    Response shape:
        {
          "status": "healthy" | "degraded",
          "version": "1.0.0",
          "checks": {
            "database": "ok" | "error: <message>",
            "redis":    "ok" | "error: <message>"
          },
          "config": {
            "openai_configured": true | false,
            "meta_configured":   true | false
          }
        }
    """
    from fastapi import Response
    from fastapi.responses import JSONResponse
    import redis as redis_client
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    checks: dict[str, str] = {}
    all_ok = True

    # ── Database check ─────────────────────────────────────────────────────
    try:
        engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        await engine.dispose()
        checks["database"] = "ok"
    except Exception as exc:
        checks["database"] = f"error: {exc}"
        all_ok = False
        logger.error("Health check — database unreachable: %s", exc)

    # ── Redis check ────────────────────────────────────────────────────────
    try:
        r = redis_client.from_url(settings.REDIS_URL, socket_connect_timeout=3)
        r.ping()
        r.close()
        checks["redis"] = "ok"
    except Exception as exc:
        checks["redis"] = f"error: {exc}"
        all_ok = False
        logger.error("Health check — Redis unreachable: %s", exc)

    payload = {
        "status": "healthy" if all_ok else "degraded",
        "version": "1.0.0",
        "checks": checks,
        "config": {
            "openai_configured": bool(settings.OPENAI_API_KEY),
            "meta_configured": bool(settings.META_APP_ID and settings.META_APP_SECRET),
        },
    }

    http_status = 200 if all_ok else 503
    return JSONResponse(content=payload, status_code=http_status)


# ── API routers ───────────────────────────────────────────────────────────────

app.include_router(auth.router,      prefix=settings.API_V1_STR)
app.include_router(org.router,       prefix=settings.API_V1_STR)
app.include_router(knowledge.router, prefix=settings.API_V1_STR)
app.include_router(fb.router,        prefix=settings.API_V1_STR)
app.include_router(bot.router,       prefix=settings.API_V1_STR)
app.include_router(resume.router,    prefix=settings.API_V1_STR)
