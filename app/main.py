"""Application entry point.

Run with:  uvicorn app.main:app --reload
"""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, bot, resume
from app.core.config import settings
from app.db.session import Base, engine
import app.models.all_models  # noqa: F401  (registers models on Base.metadata)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Dev convenience: auto-create tables on startup.
    # For real schema changes, use Alembic migrations instead.
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title=settings.PROJECT_NAME,
    openapi_url=f"{settings.API_V1_STR}/openapi.json",
    lifespan=lifespan,
)

# ── CORS ───────────────────────────────────────────────
if settings.BACKEND_CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.BACKEND_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


# ── Health / root ──────────────────────────────────────
@app.get("/")
def root():
    return {"project": settings.PROJECT_NAME, "docs": "/docs"}


@app.get("/health")
def health():
    return {"status": "ok"}


# ── Routers ────────────────────────────────────────────
app.include_router(auth.router, prefix=settings.API_V1_STR)
app.include_router(bot.router, prefix=settings.API_V1_STR)
app.include_router(resume.router, prefix=settings.API_V1_STR)
