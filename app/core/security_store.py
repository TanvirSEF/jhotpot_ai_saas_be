"""Redis-backed security controls shared by authentication and OAuth flows."""

import hashlib
import logging
from typing import Any

import redis.asyncio as redis
from redis.exceptions import RedisError

from app.core.config import settings

logger = logging.getLogger(__name__)

_RATE_LIMIT_SCRIPT = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
local ttl = redis.call('TTL', KEYS[1])
return {current, ttl}
"""

_CONSUME_ONCE_SCRIPT = """
if redis.call('GET', KEYS[1]) then
    redis.call('DEL', KEYS[1])
    return 1
end
return 0
"""


class AuthRateLimitExceeded(Exception):
    def __init__(self, retry_after: int):
        super().__init__("Authentication rate limit exceeded")
        self.retry_after = retry_after


class SecurityStoreUnavailable(Exception):
    """Raised when a security decision cannot be made because Redis failed."""


def _identity_digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]


def _client_ip(value: str | None) -> str:
    return value.strip() if value and value.strip() else "unknown"


async def _close_if_owned(client: Any, owned: bool) -> None:
    if owned:
        await client.aclose()


async def _increment_window(client: Any, key: str, window_seconds: int) -> tuple[int, int]:
    current, ttl = await client.eval(
        _RATE_LIMIT_SCRIPT,
        1,
        key,
        window_seconds,
    )
    return int(current), max(int(ttl), 1)


async def enforce_auth_rate_limit(
    *,
    action: str,
    client_ip: str | None,
    account_identifier: str,
    redis_client: Any | None = None,
) -> None:
    """Apply independent per-IP and per-account fixed-window limits."""
    client = redis_client or redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    owned = redis_client is None
    namespace = f"nexussuite:security:auth:{action}"
    ip_key = f"{namespace}:ip:{_identity_digest(_client_ip(client_ip))}"
    account_key = (
        f"{namespace}:account:{_identity_digest(account_identifier.lower())}"
    )

    try:
        ip_count, ip_ttl = await _increment_window(
            client,
            ip_key,
            settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
        )
        account_count, account_ttl = await _increment_window(
            client,
            account_key,
            settings.AUTH_RATE_LIMIT_WINDOW_SECONDS,
        )
    except RedisError as exc:
        logger.error("Redis security store unavailable during %s", action)
        raise SecurityStoreUnavailable from exc
    finally:
        await _close_if_owned(client, owned)

    if ip_count > settings.AUTH_RATE_LIMIT_IP_REQUESTS:
        raise AuthRateLimitExceeded(retry_after=ip_ttl)
    if account_count > settings.AUTH_RATE_LIMIT_ACCOUNT_REQUESTS:
        raise AuthRateLimitExceeded(retry_after=account_ttl)


async def register_oauth_state(
    state_id: str,
    *,
    ttl_seconds: int,
    redis_client: Any | None = None,
) -> None:
    """Register an OAuth state identifier that may be consumed exactly once."""
    client = redis_client or redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    owned = redis_client is None
    key = f"nexussuite:security:oauth-state:{state_id}"
    try:
        stored = await client.set(key, "pending", ex=ttl_seconds, nx=True)
    except RedisError as exc:
        logger.error("Redis security store unavailable during OAuth state creation")
        raise SecurityStoreUnavailable from exc
    finally:
        await _close_if_owned(client, owned)

    if not stored:
        raise SecurityStoreUnavailable("OAuth state identifier collision")


async def consume_oauth_state(
    state_id: str,
    *,
    redis_client: Any | None = None,
) -> bool:
    """Atomically consume a registered OAuth state identifier."""
    client = redis_client or redis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=2,
    )
    owned = redis_client is None
    key = f"nexussuite:security:oauth-state:{state_id}"
    try:
        consumed = await client.eval(_CONSUME_ONCE_SCRIPT, 1, key)
        return bool(consumed)
    except RedisError as exc:
        logger.error("Redis security store unavailable during OAuth state callback")
        raise SecurityStoreUnavailable from exc
    finally:
        await _close_if_owned(client, owned)
