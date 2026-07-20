import asyncio
import json
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI
from prometheus_client import generate_latest

from app.core.logging import JsonFormatter
from app.core.observability import (
    bind_request_id,
    bind_task_id,
    observe_operation,
    reset_request_id,
    reset_task_id,
)
from app.middleware.request_id import RequestIDMiddleware, normalize_request_id
from app.services.health import ReadinessResult, check_readiness
from app.worker.observability import task_finished, task_started


class ObservabilityTests(unittest.IsolatedAsyncioTestCase):
    def test_json_logs_include_bounded_correlation_context(self):
        request_token = bind_request_id("request-123")
        task_token = bind_task_id("task-456")
        try:
            record = logging.LogRecord(
                "test.logger",
                logging.INFO,
                __file__,
                1,
                "Operation %s",
                ("complete",),
                None,
            )
            record.event = "test_event"
            payload = json.loads(JsonFormatter().format(record))
        finally:
            reset_task_id(task_token)
            reset_request_id(request_token)

        self.assertEqual(payload["request_id"], "request-123")
        self.assertEqual(payload["task_id"], "task-456")
        self.assertEqual(payload["event"], "test_event")
        self.assertEqual(payload["message"], "Operation complete")

    def test_untrusted_request_ids_are_replaced(self):
        self.assertEqual(normalize_request_id("safe.request-1"), "safe.request-1")
        generated = normalize_request_id("bad\nforged-log-entry")
        self.assertNotIn("\n", generated)
        self.assertNotEqual(generated, "bad\nforged-log-entry")
        self.assertNotEqual(normalize_request_id("x" * 129), "x" * 129)

    async def test_http_metrics_use_route_template_not_resource_id(self):
        mini_app = FastAPI()
        mini_app.add_middleware(RequestIDMiddleware)

        @mini_app.get("/items/{item_id}")
        async def item(item_id: str):
            return {"id": item_id}

        transport = httpx.ASGITransport(app=mini_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            response = await client.get(
                "/items/private-resource-987",
                headers={"X-Request-ID": "request-abc"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["X-Request-ID"], "request-abc")
        metrics = generate_latest().decode("utf-8")
        self.assertIn('route="/items/{item_id}"', metrics)
        self.assertNotIn("private-resource-987", metrics)

    async def test_monitoring_endpoints_separate_live_and_ready(self):
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://test"
        ) as client:
            live = await client.get("/live")
            with patch(
                "app.services.health.check_readiness",
                AsyncMock(
                    return_value=ReadinessResult(
                        ready=False,
                        checks={"database": "ok", "redis": "unavailable"},
                    )
                ),
            ):
                ready = await client.get("/ready")
                health = await client.get("/health")
            metrics_response = await client.get("/metrics")

        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["status"], "alive")
        self.assertEqual(ready.status_code, 503)
        self.assertEqual(ready.json()["status"], "not_ready")
        self.assertEqual(health.status_code, 503)
        self.assertIn("nexussuite_http_requests_total", metrics_response.text)

    async def test_readiness_returns_sanitized_dependency_states(self):
        with (
            patch("app.services.health._probe_database", AsyncMock()),
            patch(
                "app.services.health._probe_redis",
                AsyncMock(side_effect=RuntimeError("redis://secret@host")),
            ),
        ):
            result = await check_readiness()

        self.assertFalse(result.ready)
        self.assertEqual(result.checks, {"database": "ok", "redis": "unavailable"})
        self.assertNotIn("secret", str(result.checks))

    async def test_readiness_probe_is_time_bounded(self):
        async def slow_probe():
            await asyncio.sleep(0.05)

        with (
            patch("app.services.health._probe_database", slow_probe),
            patch("app.services.health._probe_redis", AsyncMock()),
            patch("app.services.health.settings.HEALTHCHECK_TIMEOUT_SECONDS", 0.001),
        ):
            result = await check_readiness()

        self.assertEqual(result.checks["database"], "timeout")
        self.assertEqual(result.checks["redis"], "ok")

    def test_operation_timer_records_bounded_labels(self):
        with observe_operation("openai", "unit_test") as timer:
            timer.set_outcome("refused")
        metrics = generate_latest().decode("utf-8")
        self.assertIn('component="openai",operation="unit_test",outcome="refused"', metrics)

    def test_celery_signals_propagate_request_and_task_context(self):
        request = SimpleNamespace(headers={"request_id": "request-1"})
        task = SimpleNamespace(request=request, name="example_task")
        task_started(task_id="task-1", task=task)
        try:
            record = logging.LogRecord(
                "test.logger", logging.INFO, __file__, 1, "inside", (), None
            )
            payload = json.loads(JsonFormatter().format(record))
            self.assertEqual(payload["request_id"], "request-1")
            self.assertEqual(payload["task_id"], "task-1")
        finally:
            task_finished(task_id="task-1", task=task, state="SUCCESS")


if __name__ == "__main__":
    unittest.main()
