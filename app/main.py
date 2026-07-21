

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
from app.models import User  # noqa: F401

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):

    validate_configuration()
    logger.info(
        "%s is ready to serve traffic", settings.PROJECT_NAME,
        extra={"event": "application_started"},
    )

    yield

    logger.info("Application shutdown complete", extra={"event": "application_stopped"})


app = FastAPI(title=settings.PROJECT_NAME, version="1.0.0", lifespan=lifespan)


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

    return await _readiness_response()


@app.get("/health", tags=["monitoring"], summary="Compatibility readiness check")
async def health() -> JSONResponse:

    return await _readiness_response()


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:

    if not settings.METRICS_ENABLED:
        return Response(status_code=404)
    return Response(content=generate_latest(), media_type=CONTENT_TYPE_LATEST)


app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(org.router, prefix=settings.API_V1_STR)
app.include_router(knowledge.router, prefix=settings.API_V1_STR)
app.include_router(fb.router, prefix=settings.API_V1_STR)
app.include_router(bot.router, prefix=settings.API_V1_STR)
app.include_router(resume.router, prefix=settings.API_V1_STR)
