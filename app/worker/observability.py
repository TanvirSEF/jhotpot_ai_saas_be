"""Celery signal hooks for task correlation, logs, and latency metrics."""

import logging
import time
from typing import Any

from celery.signals import task_postrun, task_prerun

from app.core.observability import (
    TASK_DURATION,
    bind_request_id,
    bind_task_id,
    reset_request_id,
    reset_task_id,
)

logger = logging.getLogger(__name__)


@task_prerun.connect(weak=False)
def task_started(task_id: str, task: Any, **_kwargs: Any) -> None:
    headers = getattr(task.request, "headers", None) or {}
    request_id = str(headers.get("request_id"))[:128] if headers.get("request_id") else None
    task.request._observability_started = time.perf_counter()
    task.request._observability_request_token = bind_request_id(request_id)
    task.request._observability_task_token = bind_task_id(str(task_id)[:255])
    logger.info(
        "Celery task started",
        extra={"event": "task_started", "operation": task.name},
    )


@task_postrun.connect(weak=False)
def task_finished(
    task_id: str,
    task: Any,
    state: str | None = None,
    **_kwargs: Any,
) -> None:
    started = getattr(task.request, "_observability_started", time.perf_counter())
    duration = max(0.0, time.perf_counter() - started)
    outcome = "success" if state == "SUCCESS" else "error"
    TASK_DURATION.labels(task.name, outcome).observe(duration)
    logger.info(
        "Celery task completed",
        extra={
            "event": "task_completed",
            "operation": task.name,
            "outcome": outcome,
            "duration_ms": round(duration * 1000, 3),
        },
    )
    request_token = getattr(task.request, "_observability_request_token", None)
    task_token = getattr(task.request, "_observability_task_token", None)
    if request_token is not None:
        reset_request_id(request_token)
    if task_token is not None:
        reset_task_id(task_token)
