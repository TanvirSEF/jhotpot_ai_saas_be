"""Shared retry policy, correlation helpers, and final-failure persistence."""

import asyncio
import logging
import random
import uuid
from typing import Any, NoReturn

import httpx
import openai
from billiard.exceptions import SoftTimeLimitExceeded
from redis.exceptions import RedisError
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError

from app.models import TaskFailure
from app.worker.db import task_db_session

logger = logging.getLogger(__name__)

_RETRYABLE_HTTP_STATUSES = {408, 409, 425, 429}


class PermanentTaskError(RuntimeError):
    """A safe, expected task failure that must not be retried."""


def is_retryable_exception(exc: BaseException) -> bool:
    """Return whether another attempt can reasonably succeed unchanged."""
    if isinstance(exc, PermanentTaskError):
        return False
    if isinstance(
        exc,
        (
            openai.RateLimitError,
            openai.APIConnectionError,
            openai.APITimeoutError,
            openai.InternalServerError,
            httpx.TimeoutException,
            httpx.NetworkError,
            httpx.RemoteProtocolError,
            OperationalError,
            SQLAlchemyTimeoutError,
            RedisError,
            SoftTimeLimitExceeded,
        ),
    ):
        return True
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        return status in _RETRYABLE_HTTP_STATUSES or status >= 500
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status in _RETRYABLE_HTTP_STATUSES or status >= 500

    # Service-specific exceptions can expose a narrow retry contract without
    # coupling this module to every integration package.
    retryable = getattr(exc, "retryable", None)
    return retryable is True


def jittered_backoff(base_seconds: int, retry_number: int) -> int:
    """Full-jitter exponential delay, capped at five minutes."""
    ceiling = min(300, base_seconds * (2 ** max(0, retry_number)))
    return max(1, round(random.uniform(0.5 * ceiling, ceiling)))


def request_id_from_task(task: Any) -> str | None:
    headers = getattr(task.request, "headers", None) or {}
    value = headers.get("request_id")
    return str(value)[:255] if value else None


def task_will_retry(task: Any, exc: BaseException) -> bool:
    retries = int(getattr(task.request, "retries", 0) or 0)
    max_retries = int(task.max_retries or 0)
    return is_retryable_exception(exc) and retries < max_retries


def correlation_headers(request: Any) -> dict[str, str]:
    """Build JSON-safe Celery headers from a FastAPI request."""
    request_id = getattr(getattr(request, "state", None), "request_id", None)
    return {"request_id": str(request_id)[:255]} if request_id else {}


def _safe_error_message(exc: BaseException) -> str:
    if isinstance(exc, PermanentTaskError):
        return str(exc)[:500]
    if isinstance(exc, (openai.APIStatusError, httpx.HTTPStatusError)):
        status = getattr(exc, "status_code", None)
        if status is None and isinstance(exc, httpx.HTTPStatusError):
            status = exc.response.status_code
        return f"External service returned HTTP {status}."
    if is_retryable_exception(exc):
        return "A transient dependency failure exhausted its retry policy."
    return "A non-retryable task failure occurred."


def _sanitize_context(context: dict[str, Any]) -> dict[str, str | int | bool | None]:
    """Keep only scalar operational identifiers and bound their storage size."""
    sanitized: dict[str, str | int | bool | None] = {}
    for key, value in context.items():
        safe_key = str(key)[:100]
        if value is None or isinstance(value, (int, bool)):
            sanitized[safe_key] = value
        else:
            sanitized[safe_key] = str(value)[:255]
    return sanitized


async def record_final_failure(
    *,
    task_id: str,
    task_name: str,
    request_id: str | None,
    safe_context: dict[str, Any],
    exc: BaseException,
    retries: int,
) -> None:
    """Insert a sanitized failure once; duplicate delivery stays idempotent."""
    async with task_db_session() as db:
        statement = insert(TaskFailure).values(
            id=uuid.uuid4(),
            task_id=task_id,
            task_name=task_name,
            request_id=request_id,
            safe_context=_sanitize_context(safe_context),
            error_type=type(exc).__name__[:255],
            error_message=_safe_error_message(exc),
            retries=retries,
        ).on_conflict_do_nothing(
            index_elements=[TaskFailure.task_id],
        )
        await db.execute(statement)
        await db.commit()


def retry_or_fail(
    task: Any,
    exc: BaseException,
    *,
    base_delay: int,
    safe_context: dict[str, Any],
) -> NoReturn:
    """Retry a transient error or durably record the terminal failure."""
    retries = int(getattr(task.request, "retries", 0) or 0)
    max_retries = int(task.max_retries or 0)
    request_id = request_id_from_task(task)
    task_id = str(getattr(task.request, "id", "unknown"))[:255]
    task_name = str(getattr(task, "name", type(task).__name__))[:255]

    if task_will_retry(task, exc):
        countdown = jittered_backoff(base_delay, retries)
        logger.warning(
            "Retrying task=%s task_id=%s request_id=%s attempt=%d/%d in=%ds error=%s",
            task_name,
            task_id,
            request_id,
            retries + 1,
            max_retries,
            countdown,
            type(exc).__name__,
        )
        raise task.retry(exc=exc, countdown=countdown)

    try:
        asyncio.run(
            record_final_failure(
                task_id=task_id,
                task_name=task_name,
                request_id=request_id,
                safe_context=safe_context,
                exc=exc,
                retries=retries,
            )
        )
    except Exception:
        # The primary dependency may be the database itself. Never hide the
        # original task exception when the audit write is also unavailable.
        logger.exception("Could not persist final task failure task_id=%s", task_id)

    logger.error(
        "Task permanently failed task=%s task_id=%s request_id=%s error=%s",
        task_name,
        task_id,
        request_id,
        type(exc).__name__,
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    raise exc
