import os
import unittest
from unittest.mock import patch

import redis.asyncio as redis
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.core.security_store import (
    AuthRateLimitExceeded,
    consume_oauth_state,
    enforce_auth_rate_limit,
    register_oauth_state,
)


RUN_REDIS_TESTS = os.getenv("RUN_REDIS_INTEGRATION_TESTS") == "1"


def _guard_disposable_redis(redis_url: str) -> None:
    url = make_url(redis_url)
    if (
        url.drivername != "redis"
        or url.host not in {"127.0.0.1", "localhost"}
        or url.port != 56379
        or url.database != "15"
    ):
        raise RuntimeError(
            "Redis integration tests require redis://127.0.0.1:56379/15"
        )


@unittest.skipUnless(
    RUN_REDIS_TESTS,
    "Set RUN_REDIS_INTEGRATION_TESTS=1 to run Redis security tests.",
)
class RedisSecurityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis_url = os.environ["TEST_REDIS_URL"]
        _guard_disposable_redis(self.redis_url)
        self.client = redis.from_url(self.redis_url, decode_responses=True)
        await self.client.flushdb()

    async def asyncTearDown(self):
        await self.client.flushdb()
        await self.client.aclose()

    async def test_real_redis_enforces_rate_limit_atomically(self):
        with patch.multiple(
            settings,
            AUTH_RATE_LIMIT_IP_REQUESTS=1,
            AUTH_RATE_LIMIT_ACCOUNT_REQUESTS=5,
            AUTH_RATE_LIMIT_WINDOW_SECONDS=60,
        ):
            await enforce_auth_rate_limit(
                action="login",
                client_ip="203.0.113.20",
                account_identifier="person@example.com",
                redis_client=self.client,
            )
            with self.assertRaises(AuthRateLimitExceeded):
                await enforce_auth_rate_limit(
                    action="login",
                    client_ip="203.0.113.20",
                    account_identifier="person@example.com",
                    redis_client=self.client,
                )

    async def test_real_redis_consumes_oauth_state_once(self):
        await register_oauth_state(
            "integration-state",
            ttl_seconds=600,
            redis_client=self.client,
        )
        self.assertTrue(
            await consume_oauth_state(
                "integration-state",
                redis_client=self.client,
            )
        )
        self.assertFalse(
            await consume_oauth_state(
                "integration-state",
                redis_client=self.client,
            )
        )


if __name__ == "__main__":
    unittest.main()
