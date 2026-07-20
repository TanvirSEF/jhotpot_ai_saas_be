import hashlib
import hmac
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from sqlalchemy import CheckConstraint

from app.api.v1.fb import webhook_ingest
from app.core.config import settings
from app.models import WebhookEvent
from app.services.webhook_inbox import (
    AcceptedWebhookEvent,
    InboxWriteResult,
    normalized_payload,
)
from app.services.webhook_parser import (
    CommentEvent,
    MessengerEvent,
    parse_webhook_payload,
)
from app.worker.celery_app import celery_app


class WebhookParserIdempotencyTests(unittest.TestCase):
    def test_parser_captures_provider_message_and_comment_ids(self):
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "page-1",
                    "messaging": [
                        {
                            "sender": {"id": "customer-1"},
                            "recipient": {"id": "page-1"},
                            "timestamp": 1000,
                            "message": {"mid": "mid.123", "text": "Hello"},
                        }
                    ],
                    "changes": [
                        {
                            "field": "feed",
                            "value": {
                                "item": "comment",
                                "verb": "add",
                                "comment_id": "comment-123",
                                "post_id": "post-123",
                                "message": "Price?",
                                "created_time": 1001,
                                "from": {"id": "customer-2", "name": "Buyer"},
                            },
                        }
                    ],
                }
            ],
        }

        events = parse_webhook_payload(payload)

        self.assertEqual([event.event_id for event in events], ["mid.123", "comment-123"])

    def test_message_without_provider_id_is_not_processed(self):
        payload = {
            "object": "page",
            "entry": [
                {
                    "id": "page-1",
                    "messaging": [
                        {
                            "sender": {"id": "customer-1"},
                            "recipient": {"id": "page-1"},
                            "timestamp": 1000,
                            "message": {"text": "Hello"},
                        }
                    ],
                }
            ],
        }

        self.assertEqual(parse_webhook_payload(payload), [])

    def test_normalized_inbox_payload_drops_raw_provider_envelope(self):
        event = MessengerEvent(
            event_id="mid.123",
            page_id="page-1",
            sender_id="customer-1",
            recipient_id="page-1",
            message_text="Hello",
            timestamp=1000,
            raw={"access_token": "must-not-be-copied"},
        )

        payload = normalized_payload(event)

        self.assertNotIn("raw", payload)
        self.assertEqual(payload["event_id"], "mid.123")
        self.assertEqual(payload["type"], "MessengerEvent")


class WebhookInboxModelTests(unittest.TestCase):
    def test_inbox_has_provider_deduplication_and_explicit_states(self):
        constraints = {
            constraint.name: str(constraint.sqltext)
            for constraint in WebhookEvent.__table__.constraints
            if isinstance(constraint, CheckConstraint)
        }
        unique_names = {constraint.name for constraint in WebhookEvent.__table__.constraints}

        self.assertIn("uq_webhook_events_provider_event", unique_names)
        self.assertIn("delivering", constraints["ck_webhook_events_state"])
        self.assertIn("succeeded", constraints["ck_webhook_events_state"])
        self.assertNotIn("raw", WebhookEvent.__table__.c)

    def test_recovery_is_scheduled_every_minute(self):
        schedule = celery_app.conf.beat_schedule["recover-meta-webhook-inbox"]

        self.assertEqual(schedule["task"], "recover_fb_webhook_inbox")
        self.assertEqual(schedule["schedule"], 60.0)


class BrokerFailureAcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_committed_event_is_acknowledged_when_publish_fails(self):
        raw_body = json.dumps({"object": "page", "entry": []}).encode()
        signature = hmac.new(
            settings.META_APP_SECRET.encode(), raw_body, hashlib.sha256
        ).hexdigest()
        request = SimpleNamespace(
            body=AsyncMock(return_value=raw_body),
            headers={"X-Hub-Signature-256": f"sha256={signature}"},
            state=SimpleNamespace(request_id="request-1"),
        )
        event_id = uuid4()
        inbox_result = InboxWriteResult(
            accepted=[AcceptedWebhookEvent(id=event_id, request_id="request-1")],
            duplicates=0,
            unregistered=0,
        )
        db = AsyncMock()

        with (
            patch("app.services.webhook_parser.parse_webhook_payload", return_value=[]),
            patch(
                "app.services.webhook_inbox.persist_webhook_events",
                new=AsyncMock(return_value=inbox_result),
            ),
            patch(
                "app.worker.tasks.process_fb_webhook.apply_async",
                side_effect=ConnectionError("broker unavailable"),
            ),
        ):
            response = await webhook_ingest(request, db)

        self.assertEqual(response["status"], "ok")
        self.assertEqual(response["events_accepted"], 1)
        self.assertEqual(response["events_queued"], 0)
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
