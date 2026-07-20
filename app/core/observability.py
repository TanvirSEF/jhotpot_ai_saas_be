"""Low-cardinality metrics and request/task correlation primitives."""

import logging
import time
from contextvars import ContextVar, Token
from functools import wraps
from typing import Any, Literal

from prometheus_client import Counter, Gauge, Histogram

_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)
_task_id: ContextVar[str | None] = ContextVar("task_id", default=None)

HTTP_REQUESTS = Counter(
    "nexussuite_http_requests_total",
    "Completed HTTP requests.",
    ("method", "route", "status_code"),
)
HTTP_DURATION = Histogram(
    "nexussuite_http_request_duration_seconds",
    "HTTP request latency by stable route template.",
    ("method", "route"),
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10),
)
HTTP_IN_PROGRESS = Gauge(
    "nexussuite_http_requests_in_progress",
    "HTTP requests currently being processed.",
    ("method",),
)
OPERATIONS = Counter(
    "nexussuite_operations_total",
    "Dependency and pipeline operations by bounded outcome.",
    ("component", "operation", "outcome"),
)
OPERATION_DURATION = Histogram(
    "nexussuite_operation_duration_seconds",
    "Dependency and pipeline operation latency.",
    ("component", "operation", "outcome"),
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)
TASK_DURATION = Histogram(
    "nexussuite_task_duration_seconds",
    "Celery task execution latency.",
    ("task", "outcome"),
    buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120),
)


def bind_request_id(value: str | None) -> Token:
    return _request_id.set(value)


def bind_task_id(value: str | None) -> Token:
    return _task_id.set(value)


def reset_request_id(token: Token) -> None:
    _request_id.reset(token)


def reset_task_id(token: Token) -> None:
    _task_id.reset(token)


def current_request_id() -> str | None:
    return _request_id.get()


def current_task_id() -> str | None:
    return _task_id.get()


class OperationTimer:
    """Record duration and outcome in both Prometheus and structured logs."""

    def __init__(self, component: str, operation: str) -> None:
        self.component = component
        self.operation = operation
        self.outcome = "success"
        self.started = 0.0

    def __enter__(self) -> "OperationTimer":
        self.started = time.perf_counter()
        return self

    def set_outcome(self, outcome: str) -> None:
        self.outcome = outcome

    def __exit__(self, exc_type: Any, _exc: Any, _tb: Any) -> Literal[False]:
        if exc_type is not None:
            self.outcome = "error"
        duration = max(0.0, time.perf_counter() - self.started)
        OPERATIONS.labels(self.component, self.operation, self.outcome).inc()
        OPERATION_DURATION.labels(
            self.component, self.operation, self.outcome
        ).observe(duration)
        logging.getLogger("app.operations").info(
            "Operation completed",
            extra={
                "event": "operation_completed",
                "component": self.component,
                "operation": self.operation,
                "outcome": self.outcome,
                "duration_ms": round(duration * 1000, 3),
            },
        )
        return False


def observe_operation(component: str, operation: str) -> OperationTimer:
    return OperationTimer(component, operation)


def observed_async(component: str, operation: str):
    """Instrument an async boundary without changing its public contract."""

    def decorator(function):
        @wraps(function)
        async def wrapped(*args, **kwargs):
            with observe_operation(component, operation):
                return await function(*args, **kwargs)

        return wrapped

    return decorator
