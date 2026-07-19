from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, bot, resume, org
from app.core.config import settings
from app.core.logging import get_logger
from app.models import User  # noqa: F401 registers models with SQLAlchemy

logger = get_logger(__name__)



@asynccontextmanager
async def lifespan(_app: FastAPI):
    import asyncio
    from alembic.config import Config
    from alembic import command
    import redis as redis_client

    def run_migrations():
        alembic_cfg = Config("alembic.ini")
        command.upgrade(alembic_cfg, "head")

    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, run_migrations)

    r = redis_client.from_url(settings.REDIS_URL, socket_connect_timeout=5)
    r.ping()
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
app.include_router(org.router, prefix=settings.API_V1_STR)
app.include_router(bot.router, prefix=settings.API_V1_STR)
app.include_router(resume.router, prefix=settings.API_V1_STR)
