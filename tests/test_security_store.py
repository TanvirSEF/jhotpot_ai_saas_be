import unittest
from unittest.mock import patch

from redis.exceptions import ConnectionError

from app.core.config import settings
from app.core.security_store import (
    AuthRateLimitExceeded,
    SecurityStoreUnavailable,
    consume_oauth_state,
    enforce_auth_rate_limit,
    register_oauth_state,
)


class FakeRedis:
    def __init__(self):
        self.counters: dict[str, int] = {}
        self.values: dict[str, str] = {}
        self.seen_keys: list[str] = []

    async def eval(self, script, _key_count, key, *args):
        self.seen_keys.append(key)
        if "INCR" in script:
            self.counters[key] = self.counters.get(key, 0) + 1
            return [self.counters[key], int(args[0])]
        if key in self.values:
            del self.values[key]
            return 1
        return 0

    async def set(self, key, value, *, ex, nx):
        self.seen_keys.append(key)
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True


class BrokenRedis(FakeRedis):
    async def eval(self, script, _key_count, key, *args):
        raise ConnectionError("Redis is unavailable")


class SecurityStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_auth_rate_limit_blocks_repeated_attempts(self):
        client = FakeRedis()
        with patch.multiple(
            settings,
            AUTH_RATE_LIMIT_IP_REQUESTS=1,
            AUTH_RATE_LIMIT_ACCOUNT_REQUESTS=10,
            AUTH_RATE_LIMIT_WINDOW_SECONDS=60,
        ):
            await enforce_auth_rate_limit(
                action="login",
                client_ip="203.0.113.10",
                account_identifier="person@example.com",
                redis_client=client,
            )
            with self.assertRaises(AuthRateLimitExceeded) as raised:
                await enforce_auth_rate_limit(
                    action="login",
                    client_ip="203.0.113.10",
                    account_identifier="person@example.com",
                    redis_client=client,
                )

        self.assertEqual(raised.exception.retry_after, 60)
        self.assertNotIn("person@example.com", " ".join(client.seen_keys))
        self.assertNotIn("203.0.113.10", " ".join(client.seen_keys))

    async def test_oauth_state_can_only_be_consumed_once(self):
        client = FakeRedis()
        await register_oauth_state(
            "state-id",
            ttl_seconds=600,
            redis_client=client,
        )

        self.assertTrue(
            await consume_oauth_state("state-id", redis_client=client)
        )
        self.assertFalse(
            await consume_oauth_state("state-id", redis_client=client)
        )

    async def test_account_limit_applies_across_different_ips(self):
        client = FakeRedis()
        with patch.multiple(
            settings,
            AUTH_RATE_LIMIT_IP_REQUESTS=10,
            AUTH_RATE_LIMIT_ACCOUNT_REQUESTS=1,
            AUTH_RATE_LIMIT_WINDOW_SECONDS=60,
        ):
            await enforce_auth_rate_limit(
                action="login",
                client_ip="203.0.113.10",
                account_identifier="person@example.com",
                redis_client=client,
            )
            with self.assertRaises(AuthRateLimitExceeded):
                await enforce_auth_rate_limit(
                    action="login",
                    client_ip="203.0.113.11",
                    account_identifier="person@example.com",
                    redis_client=client,
                )

    async def test_security_store_failure_is_not_silently_ignored(self):
        with self.assertRaises(SecurityStoreUnavailable):
            await enforce_auth_rate_limit(
                action="login",
                client_ip="203.0.113.10",
                account_identifier="person@example.com",
                redis_client=BrokenRedis(),
            )


if __name__ == "__main__":
    unittest.main()
