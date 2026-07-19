from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, bot, resume
from app.core.config import settings
from app.db.session import Base, engine
from app.models import all_models  # noqa: F401  registers tables


@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    from alembic.config import Config
    from alembic import command
    import redis as redis_client

    # ── 1. Run pending Alembic migrations ──────────────────────────────────
    def run_migrations():
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_migrations)

    # ── 2. Verify Redis connectivity (fail-fast) ───────────────────────────
    r = redis_client.from_url(settings.REDIS_URL, socket_connect_timeout=5)
    r.ping()  # raises ConnectionError if Redis is unreachable
    r.close()

    yield


app = FastAPI(title=settings.PROJECT_NAME, lifespan=lifespan)

if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


@app.get("/")
def index():
    return {"name": settings.PROJECT_NAME}


@app.get("/health")
def health():
    return {"status": "ok"}


app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(bot.router, prefix=settings.API_V1_STR)
app.include_router(resume.router, prefix=settings.API_V1_STR)
