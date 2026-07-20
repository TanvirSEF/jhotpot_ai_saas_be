"""Database sessions owned by short-lived Celery task event loops."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


@asynccontextmanager
async def task_db_session() -> AsyncIterator[AsyncSession]:
    """Yield an isolated session and always dispose its engine afterwards."""
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
