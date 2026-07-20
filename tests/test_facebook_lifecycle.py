import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx

from app.models import FbPage
from app.api.v1.fb import disconnect_page
from app.services.meta import (
    MetaAPIError,
    REQUIRED_PAGE_SUBSCRIPTIONS,
    REQUIRED_PAGE_TOKEN_SCOPES,
    _raise_for_meta_error,
    evaluate_token_health,
    subscribe_page_webhooks,
)


class FacebookLifecycleModelTests(unittest.TestCase):
    def test_disconnected_page_can_exist_without_a_credential(self):
        token_column = FbPage.__table__.c.encrypted_access_token

        self.assertTrue(token_column.nullable)
        self.assertFalse(FbPage.__table__.c.is_bot_active.default.arg)
        self.assertIn("connection_status", FbPage.__table__.c)
        self.assertIn("subscription_status", FbPage.__table__.c)
        self.assertIn("token_status", FbPage.__table__.c)
        self.assertIn("disconnected_at", FbPage.__table__.c)


class TokenHealthTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 20, tzinfo=timezone.utc)
        self.valid_data = {
            "is_valid": True,
            "scopes": list(REQUIRED_PAGE_TOKEN_SCOPES),
            "expires_at": int((self.now + timedelta(days=30)).timestamp()),
            "data_access_expires_at": int(
                (self.now + timedelta(days=60)).timestamp()
            ),
        }

    def test_valid_page_token_does_not_require_oauth_only_show_list_scope(self):
        health = evaluate_token_health(self.valid_data, now=self.now)

        self.assertEqual(health.status, "valid")
        self.assertEqual(health.missing_scopes, [])

    def test_missing_operational_scope_requires_reauthorization(self):
        data = dict(self.valid_data)
        data["scopes"] = [
            scope
            for scope in REQUIRED_PAGE_TOKEN_SCOPES
            if scope != "pages_messaging"
        ]

        health = evaluate_token_health(data, now=self.now)

        self.assertEqual(health.status, "insufficient_scope")
        self.assertEqual(health.missing_scopes, ["pages_messaging"])

    def test_expired_data_access_is_treated_as_expired(self):
        data = dict(self.valid_data)
        data["data_access_expires_at"] = int(
            (self.now - timedelta(seconds=1)).timestamp()
        )

        health = evaluate_token_health(data, now=self.now)

        self.assertEqual(health.status, "expired")

    def test_meta_invalid_flag_wins_over_scope_information(self):
        data = dict(self.valid_data, is_valid=False, scopes=[])

        health = evaluate_token_health(data, now=self.now)

        self.assertEqual(health.status, "invalid")


class MetaErrorTests(unittest.TestCase):
    def test_provider_message_is_not_exposed_to_clients(self):
        request = httpx.Request("GET", "https://graph.facebook.com/test")
        response = httpx.Response(
            400,
            request=request,
            json={
                "error": {
                    "message": "raw provider diagnostic with sensitive context",
                    "code": 190,
                    "is_transient": False,
                }
            },
        )

        with self.assertRaises(MetaAPIError) as raised:
            _raise_for_meta_error(response, "debug token")

        self.assertEqual(raised.exception.meta_code, 190)
        self.assertNotIn("sensitive", raised.exception.detail)
        self.assertEqual(
            raised.exception.detail,
            "Meta API request failed during debug token.",
        )


class PageSubscriptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_subscribe_requests_required_fields_and_verifies_result(self):
        response = httpx.Response(
            200,
            request=httpx.Request("POST", "https://graph.facebook.com/page"),
            json={"success": True},
        )
        client = AsyncMock()
        client.post.return_value = response
        client_context = AsyncMock()
        client_context.__aenter__.return_value = client
        client_context.__aexit__.return_value = None

        with (
            patch("app.services.meta.httpx.AsyncClient", return_value=client_context),
            patch("app.services.meta.get_page_subscription", new_callable=AsyncMock) as get_subscription,
        ):
            from app.services.meta import PageSubscription

            get_subscription.return_value = PageSubscription(
                subscribed=True,
                fields=list(REQUIRED_PAGE_SUBSCRIPTIONS),
            )
            subscription = await subscribe_page_webhooks("page-1", "page-token")

        self.assertTrue(subscription.subscribed)
        client.post.assert_awaited_once()
        call = client.post.await_args
        self.assertEqual(
            call.kwargs["params"]["subscribed_fields"],
            ",".join(REQUIRED_PAGE_SUBSCRIPTIONS),
        )
        self.assertEqual(
            call.kwargs["headers"]["Authorization"],
            "Bearer page-token",
        )
        get_subscription.assert_awaited_once_with("page-1", "page-token")


class DisconnectLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_disconnect_succeeds_when_remote_unsubscribe_fails(self):
        record_id = uuid4()
        page = SimpleNamespace(
            page_id="page-1",
            encrypted_access_token="encrypted",
            is_bot_active=True,
            connection_status="connected",
            subscription_status="subscribed",
            token_status="valid",
            subscribed_fields=["messages", "feed"],
            disconnected_at=None,
            last_error_code=None,
        )
        db = AsyncMock()

        with (
            patch(
                "app.api.v1.fb._get_page_for_user",
                new=AsyncMock(return_value=page),
            ),
            patch("app.api.v1.fb.decrypt_token", return_value="page-token"),
            patch(
                "app.api.v1.fb.unsubscribe_page_webhooks",
                new=AsyncMock(
                    side_effect=MetaAPIError(
                        "unsubscribe Page webhooks",
                        meta_code=2,
                        transient=True,
                    )
                ),
            ),
        ):
            result = await disconnect_page(
                record_id,
                current_user=SimpleNamespace(id=uuid4()),
                db=db,
            )

        self.assertIsNone(result)
        self.assertIsNone(page.encrypted_access_token)
        self.assertFalse(page.is_bot_active)
        self.assertEqual(page.connection_status, "disconnected")
        self.assertEqual(page.subscription_status, "failed")
        self.assertEqual(page.token_status, "missing")
        self.assertEqual(page.last_error_code, "meta_unsubscribe_2")
        db.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
