"""NexusSuite FastAPI application entry point."""

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.v1 import auth, bot, fb, knowledge, org, resume
from app.core.config import settings
from app.core.logging import get_logger
from app.core.startup_checks import validate_configuration
from app.middleware.request_id import RequestIDMiddleware
from app.models import User  # noqa: F401 - register SQLAlchemy models

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Validate config, migrate the database, and verify Redis at startup."""
    import asyncio

    import redis as redis_client
    from alembic import command
    from alembic.config import Config

    validate_configuration()

    def run_migrations() -> None:
        command.upgrade(Config("alembic.ini"), "head")

    await asyncio.get_running_loop().run_in_executor(None, run_migrations)
    logger.info("Database migrations applied", extra={"event": "migrations_applied"})

    redis = redis_client.from_url(settings.REDIS_URL, socket_connect_timeout=5)
    try:
        redis.ping()
    finally:
        redis.close()
    logger.info("Redis connection verified", extra={"event": "redis_verified"})
    logger.info(
        "%s is ready to serve traffic", settings.PROJECT_NAME,
        extra={"event": "application_started"},
    )

    yield

    logger.info("Application shutdown complete", extra={"event": "application_stopped"})


app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0", lifespan=lifespan)

# Starlette evaluates middleware in reverse registration order. CORS is added
# first so RequestIDMiddleware remains the outer observability boundary.
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
app.add_middleware(RequestIDMiddleware)


@app.get("/", include_in_schema=False)
def index() -> dict[str, str]:
    return {"name": settings.PROJECT_NAME, "version": "1.0.0"}


@app.get("/live", tags=["monitoring"], summary="Process liveness")
async def liveness() -> dict[str, str]:
    """Return immediately; dependency failures must not trigger restarts."""
    return {"status": "alive", "version": "1.0.0"}


async def _readiness_response() -> JSONResponse:
    from app.services.health import check_readiness

    result = await check_readiness()
    return JSONResponse(
        content={
            "status": "ready" if result.ready else "not_ready",
            "version": "1.0.0",
            "checks": result.checks,
        },
        status_code=200 if result.ready else 503,
    )


@app.get("/ready", tags=["monitoring"], summary="Dependency readiness")
async def readiness() -> JSONResponse:
    """Probe PostgreSQL and Redis with strict time bounds."""
    return await _readiness_response()


@app.get("/health", tags=["monitoring"], summary="Compatibility readiness check")
async def health() -> JSONResponse:
    """Backward-compatible alias for readiness checks."""
    return await _readiness_response()


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    """Expose Prometheus text metrics without application payload data."""
    if not settings.METRICS_ENABLED:
        return Response(status_code=404)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(org.router, prefix=settings.API_V1_STR)
app.include_router(knowledge.router, prefix=settings.API_V1_STR)
app.include_router(fb.router, prefix=settings.API_V1_STR)
app.include_router(bot.router, prefix=settings.API_V1_STR)
app.include_router(resume.router, prefix=settings.API_V1_STR)
