"""
Meta Graph API service layer — Phase A2.

Responsibilities:
  - Build the Facebook OAuth 2.0 authorization URL.
  - Exchange an OAuth authorization code for a short-lived user access token.
  - Upgrade a short-lived token to a long-lived token (60-day expiry).
  - Fetch all Facebook Pages the user manages (with page-level access tokens).
  - Debug / inspect a token (used for health checks).

Design decisions:
  - Async httpx.AsyncClient is used for all HTTP calls. A single client is
    shared per-request via dependency injection or direct call; it is NOT
    held as a module-level singleton because each OAuth flow is a one-shot
    operation and we want explicit connection lifecycle control.
  - All Graph API version references are pinned via GRAPH_VERSION so a
    single constant controls upgrade.
  - Errors from Meta are surfaced as HTTPException so FastAPI returns a
    consistent JSON error response to the frontend.
  - This module is deliberately free of SQLAlchemy / DB concerns. The
    endpoint layer handles persistence.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

# ── Constants ──────────────────────────────────────────────────────────────────
GRAPH_VERSION = "v20.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG_BASE = "https://www.facebook.com/{version}/dialog/oauth".format(
    version=GRAPH_VERSION
)

# Permissions the merchant must grant so the bot can read & reply
REQUIRED_PAGE_TOKEN_SCOPES = (
    "pages_messaging",
    "pages_manage_metadata",
    "pages_read_engagement",
    "pages_manage_engagement",
)
REQUIRED_PAGE_SCOPES = (
    *REQUIRED_PAGE_TOKEN_SCOPES,
    "pages_show_list",
)
REQUIRED_PAGE_SUBSCRIPTIONS = ("messages", "feed")
OAUTH_SCOPES = ",".join(REQUIRED_PAGE_SCOPES)

_TRANSIENT_META_CODES = {1, 2, 4, 17, 32, 341, 613}


class MetaAPIError(HTTPException):
    """Sanitized Meta error carrying only operational classification."""

    def __init__(self, context: str, *, meta_code: int = 0, transient: bool = False):
        self.meta_code = meta_code
        self.transient = transient
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Meta API request failed during {context}.",
        )


# ── Data shapes ────────────────────────────────────────────────────────────────

@dataclass
class PageData:
    """Normalized representation of a Facebook Page from /me/accounts."""
    page_id: str
    page_name: str
    access_token: str   # plain-text page access token — caller must encrypt


@dataclass
class PageSubscription:
    subscribed: bool
    fields: list[str]


@dataclass
class TokenHealth:
    status: str
    missing_scopes: list[str]
    expires_at: datetime | None
    data_access_expires_at: datetime | None


def _epoch_datetime(value: object) -> datetime | None:
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp > 0 else None


def evaluate_token_health(data: dict, *, now: datetime | None = None) -> TokenHealth:
    """Normalize debug_token output into the application's lifecycle states."""
    checked_at = now or datetime.now(timezone.utc)
    expires_at = _epoch_datetime(data.get("expires_at"))
    data_access_expires_at = _epoch_datetime(data.get("data_access_expires_at"))
    scopes = set(data.get("scopes") or [])
    missing_scopes = sorted(set(REQUIRED_PAGE_TOKEN_SCOPES) - scopes)

    if not data.get("is_valid", False):
        token_status = "invalid"
    elif expires_at is not None and expires_at <= checked_at:
        token_status = "expired"
    elif data_access_expires_at is not None and data_access_expires_at <= checked_at:
        token_status = "expired"
    elif missing_scopes:
        token_status = "insufficient_scope"
    else:
        token_status = "valid"
    return TokenHealth(
        status=token_status,
        missing_scopes=missing_scopes,
        expires_at=expires_at,
        data_access_expires_at=data_access_expires_at,
    )


# ── Helpers ────────────────────────────────────────────────────────────────────

def _callback_uri() -> str:
    """Canonical redirect URI registered in the Meta App Dashboard."""
    return f"{settings.BACKEND_URL}/api/v1/fb/callback"


def _raise_for_meta_error(response: httpx.Response, context: str) -> None:
    """
    Parse Meta Graph API error envelope and surface it as an HTTPException.

    Meta always returns errors as:
      {"error": {"message": "...", "type": "...", "code": ..., ...}}
    """
    try:
        data = response.json()
    except ValueError:
        if response.is_error:
            raise MetaAPIError(context, transient=response.status_code >= 500)
        return

    if "error" in data:
        err = data["error"]
        code = int(err.get("code", 0) or 0)
        transient = bool(err.get("is_transient")) or code in _TRANSIENT_META_CODES
        logger.error("[Meta API] %s failed code=%s transient=%s", context, code, transient)
        raise MetaAPIError(
            context,
            meta_code=code,
            transient=transient,
        )
    if response.is_error:
        raise MetaAPIError(context, transient=response.status_code >= 500)


# ── Public API ─────────────────────────────────────────────────────────────────

def build_oauth_url(state: str) -> str:
    """
    Build the Facebook OAuth dialog URL.

    Args:
        state: CSRF-protection token (JWT-signed, contains org_id + expiry).
               Generated by the endpoint layer.

    Returns:
        Full URL to redirect the merchant's browser to.
    """
    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": _callback_uri(),
        "scope": OAUTH_SCOPES,
        "response_type": "code",
        "state": state,
    }
    return f"{DIALOG_BASE}?{urlencode(params)}"


async def exchange_code_for_user_token(code: str) -> str:
    """
    Exchange the authorization code (from the OAuth callback) for a
    short-lived user access token.

    Meta endpoint: GET /oauth/access_token
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "redirect_uri": _callback_uri(),
                "code": code,
            },
        )
        _raise_for_meta_error(response, "code→token exchange")
        data = response.json()
        token = data.get("access_token")
        if not token:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Meta did not return an access token.",
            )
        logger.info("Short-lived user token obtained successfully.")
        return token


async def upgrade_to_long_lived_token(short_lived_token: str) -> str:
    """
    Exchange a short-lived user token (1-2 hours) for a long-lived token
    (~60 days). Long-lived tokens should be used to obtain permanent Page
    Access Tokens via /me/accounts.

    Meta endpoint: GET /oauth/access_token?grant_type=fb_exchange_token
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GRAPH_BASE}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": settings.META_APP_ID,
                "client_secret": settings.META_APP_SECRET,
                "fb_exchange_token": short_lived_token,
            },
        )
        _raise_for_meta_error(response, "token upgrade")
        data = response.json()
        long_lived = data.get("access_token")
        if not long_lived:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail="Meta did not return a long-lived token.",
            )
        logger.info("Long-lived user token obtained. Expires in ~60 days.")
        return long_lived


async def get_managed_pages(user_token: str) -> list[PageData]:
    """
    Fetch all Facebook Pages the authenticated user manages, along with
    their individual Page Access Tokens.

    Page Access Tokens obtained via /me/accounts with a long-lived user
    token are permanent (no expiry) unless the user revokes permissions.

    Meta endpoint: GET /me/accounts
    """
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GRAPH_BASE}/me/accounts",
            params={
                "access_token": user_token,
                "fields": "id,name,access_token",
            },
        )
        _raise_for_meta_error(response, "fetch managed pages")
        data = response.json()

    pages = []
    for page in data.get("data", []):
        if not page.get("access_token"):
            logger.warning("Page %s has no access token — skipping.", page.get("id"))
            continue
        pages.append(PageData(
            page_id=page["id"],
            page_name=page.get("name", ""),
            access_token=page["access_token"],
        ))

    logger.info("Retrieved %d managed page(s) from Meta.", len(pages))
    return pages


async def debug_token(token: str) -> dict:
    """
    Inspect a token's validity, expiry, and granted scopes via the
    Graph API debug_token endpoint. Useful for health checks.

    Returns raw Meta response dict.
    """
    app_token = f"{settings.META_APP_ID}|{settings.META_APP_SECRET}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{GRAPH_BASE}/debug_token",
            params={"input_token": token, "access_token": app_token},
        )
        _raise_for_meta_error(response, "debug token")
        return response.json().get("data", {})


async def get_page_subscription(
    page_id: str,
    page_access_token: str,
) -> PageSubscription:
    """Return this app's current Page webhook fields."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            f"{GRAPH_BASE}/{page_id}/subscribed_apps",
            params={"fields": "id,subscribed_fields"},
            headers={"Authorization": f"Bearer {page_access_token}"},
        )
        _raise_for_meta_error(response, "fetch Page subscription")

    for app in response.json().get("data", []):
        if str(app.get("id")) == str(settings.META_APP_ID):
            return PageSubscription(
                subscribed=True,
                fields=sorted(set(app.get("subscribed_fields") or [])),
            )
    return PageSubscription(subscribed=False, fields=[])


async def subscribe_page_webhooks(
    page_id: str,
    page_access_token: str,
) -> PageSubscription:
    """Subscribe and verify the fields used by this application's parser."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(
            f"{GRAPH_BASE}/{page_id}/subscribed_apps",
            params={"subscribed_fields": ",".join(REQUIRED_PAGE_SUBSCRIPTIONS)},
            headers={"Authorization": f"Bearer {page_access_token}"},
        )
        _raise_for_meta_error(response, "subscribe Page webhooks")
        if response.json().get("success") is not True:
            raise MetaAPIError("subscribe Page webhooks")

    subscription = await get_page_subscription(page_id, page_access_token)
    missing = set(REQUIRED_PAGE_SUBSCRIPTIONS) - set(subscription.fields)
    if not subscription.subscribed or missing:
        raise MetaAPIError("verify Page webhook subscription")
    return subscription


async def unsubscribe_page_webhooks(
    page_id: str,
    page_access_token: str,
) -> None:
    """Remove this app's webhook subscription from a Page."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.delete(
            f"{GRAPH_BASE}/{page_id}/subscribed_apps",
            headers={"Authorization": f"Bearer {page_access_token}"},
        )
        _raise_for_meta_error(response, "unsubscribe Page webhooks")
        if response.json().get("success") is not True:
            raise MetaAPIError("unsubscribe Page webhooks")
