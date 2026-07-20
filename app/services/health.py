"""Bounded readiness probes with sanitized results."""

import asyncio
from dataclasses import dataclass

import redis.asyncio as redis_async
from sqlalchemy import text

from app.core.config import settings
from app.core.observability import observe_operation
from app.db.session import engine


@dataclass(frozen=True)
class ReadinessResult:
    ready: bool
    checks: dict[str, str]


async def _probe_database() -> None:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


async def _probe_redis() -> None:
    client = redis_async.from_url(
        settings.REDIS_URL,
        socket_connect_timeout=settings.HEALTHCHECK_TIMEOUT_SECONDS,
        socket_timeout=settings.HEALTHCHECK_TIMEOUT_SECONDS,
    )
    try:
        await client.ping()
    finally:
        await client.aclose()


async def _bounded_probe(name: str, probe) -> str:
    try:
        with observe_operation("readiness", name):
            await asyncio.wait_for(
                probe(), timeout=settings.HEALTHCHECK_TIMEOUT_SECONDS
            )
    except asyncio.TimeoutError:
        return "timeout"
    except Exception:
        return "unavailable"
    return "ok"


async def check_readiness() -> ReadinessResult:
    database, redis = await asyncio.gather(
        _bounded_probe("database", _probe_database),
        _bounded_probe("redis", _probe_redis),
    )
    checks = {"database": database, "redis": redis}
    return ReadinessResult(
        ready=all(value == "ok" for value in checks.values()),
        checks=checks,
    )
