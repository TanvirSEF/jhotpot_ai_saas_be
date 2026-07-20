import unittest
from unittest.mock import patch

import httpx
from sqlalchemy.dialects import postgresql

from app.models import KnowledgeEmbedding, TaskFailure
from app.services.graph_api import GraphAPIError, _raise_for_reply_error
from app.worker.celery_app import celery_app
from app.worker.reliability import (
    PermanentTaskError,
    _safe_error_message,
    _sanitize_context,
    correlation_headers,
    is_retryable_exception,
    jittered_backoff,
)


class WorkerReliabilityTests(unittest.TestCase):
    def test_retry_classifier_separates_transient_and_permanent_errors(self):
        request = httpx.Request("GET", "https://example.test")
        transient_response = httpx.Response(503, request=request)
        permanent_response = httpx.Response(400, request=request)

        self.assertTrue(
            is_retryable_exception(
                httpx.HTTPStatusError("unavailable", request=request, response=transient_response)
            )
        )
        self.assertFalse(
            is_retryable_exception(
                httpx.HTTPStatusError("bad request", request=request, response=permanent_response)
            )
        )
        self.assertFalse(is_retryable_exception(PermanentTaskError("invalid input")))
        self.assertFalse(is_retryable_exception(ValueError("invalid UUID")))

    def test_backoff_uses_bounded_full_jitter(self):
        with patch("app.worker.reliability.random.uniform", return_value=7.6) as uniform:
            self.assertEqual(jittered_backoff(10, 0), 8)
        uniform.assert_called_once_with(5.0, 10)
        self.assertLessEqual(jittered_backoff(100, 10), 300)

    def test_graph_error_exposes_only_retry_contract(self):
        request = httpx.Request("POST", "https://graph.facebook.test")
        response = httpx.Response(
            500,
            request=request,
            json={"error": {"message": "token=secret", "code": 2, "is_transient": True}},
        )

        with self.assertRaises(GraphAPIError) as caught:
            _raise_for_reply_error(response)

        self.assertTrue(caught.exception.retryable)
        self.assertNotIn("secret", str(caught.exception))
        self.assertTrue(is_retryable_exception(caught.exception))

    def test_request_id_becomes_a_task_header(self):
        request = type(
            "Request",
            (),
            {"state": type("State", (), {"request_id": "request-123"})()},
        )()
        self.assertEqual(correlation_headers(request), {"request_id": "request-123"})

    def test_tasks_have_hard_and_soft_time_limits(self):
        expected = {
            "generate_embeddings": (45, 60),
            "process_fb_webhook": (60, 75),
            "export_resume_pdf": (90, 120),
        }
        for task_name, limits in expected.items():
            task = celery_app.tasks[task_name]
            self.assertEqual((task.soft_time_limit, task.time_limit), limits)

    def test_failure_model_contains_only_sanitized_context_column(self):
        columns = set(TaskFailure.__table__.columns.keys())
        self.assertIn("safe_context", columns)
        self.assertNotIn("task_args", columns)
        self.assertNotIn("task_kwargs", columns)

    def test_failure_payload_bounds_context_and_does_not_copy_exception_text(self):
        context = _sanitize_context({"entity_id": "x" * 500, "attempt": 3})
        self.assertEqual(len(context["entity_id"]), 255)
        self.assertEqual(context["attempt"], 3)

        error = RuntimeError("access_token=super-secret customer message")
        self.assertNotIn("super-secret", _safe_error_message(error))
        self.assertNotIn("customer message", _safe_error_message(error))

    def test_embedding_upsert_targets_source_uniqueness_constraint(self):
        from sqlalchemy.dialects.postgresql import insert

        statement = insert(KnowledgeEmbedding).values(
            org_id="00000000-0000-0000-0000-000000000001",
            entity_type="product",
            entity_id="00000000-0000-0000-0000-000000000002",
            content="test",
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_knowledge_embeddings_entity",
            set_={"content": statement.excluded.content},
        )
        sql = str(statement.compile(dialect=postgresql.dialect()))
        self.assertIn("ON CONFLICT ON CONSTRAINT uq_knowledge_embeddings_entity", sql)


if __name__ == "__main__":
    unittest.main()
