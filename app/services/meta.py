


import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, status

from app.core.config import settings
from app.core.observability import observed_async

logger = logging.getLogger(__name__)


GRAPH_VERSION = "v20.0"
GRAPH_BASE = f"https://graph.facebook.com/{GRAPH_VERSION}"
DIALOG_BASE = "https://www.facebook.com/{version}/dialog/oauth".format(
    version=GRAPH_VERSION
)


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


    def __init__(self, context: str, *, meta_code: int = 0, transient: bool = False):
        self.meta_code = meta_code
        self.transient = transient
        super().__init__(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Meta API request failed during {context}.",
        )


@dataclass
class PageData:

    page_id: str
    page_name: str
    access_token: str


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
    if not isinstance(value, (str, bytes, bytearray, int, float)):
        return None
    try:
        timestamp = int(value or 0)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc) if timestamp > 0 else None


def evaluate_token_health(data: dict, *, now: datetime | None = None) -> TokenHealth:

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


def _callback_uri() -> str:

    return f"{settings.BACKEND_URL}/api/v1/fb/callback"


def _raise_for_meta_error(response: httpx.Response, context: str) -> None:


    try:
        data = response.json()
    except ValueError:
        if response.is_error:
            raise MetaAPIError(
                context, transient=response.status_code >= 500
            ) from None
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


def build_oauth_url(state: str) -> str:


    params = {
        "client_id": settings.META_APP_ID,
        "redirect_uri": _callback_uri(),
        "scope": OAUTH_SCOPES,
        "response_type": "code",
        "state": state,
    }
    return f"{DIALOG_BASE}?{urlencode(params)}"


@observed_async("meta", "oauth_code_exchange")
async def exchange_code_for_user_token(code: str) -> str:


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


@observed_async("meta", "token_upgrade")
async def upgrade_to_long_lived_token(short_lived_token: str) -> str:


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


@observed_async("meta", "managed_pages")
async def get_managed_pages(user_token: str) -> list[PageData]:


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


@observed_async("meta", "debug_token")
async def debug_token(token: str) -> dict:


    app_token = f"{settings.META_APP_ID}|{settings.META_APP_SECRET}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.get(
            f"{GRAPH_BASE}/debug_token",
            params={"input_token": token, "access_token": app_token},
        )
        _raise_for_meta_error(response, "debug token")
        return response.json().get("data", {})


@observed_async("meta", "subscription_read")
async def get_page_subscription(
    page_id: str,
    page_access_token: str,
) -> PageSubscription:

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


@observed_async("meta", "subscription_write")
async def subscribe_page_webhooks(
    page_id: str,
    page_access_token: str,
) -> PageSubscription:

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


@observed_async("meta", "subscription_delete")
async def unsubscribe_page_webhooks(
    page_id: str,
    page_access_token: str,
) -> None:

    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.delete(
            f"{GRAPH_BASE}/{page_id}/subscribed_apps",
            headers={"Authorization": f"Bearer {page_access_token}"},
        )
        _raise_for_meta_error(response, "unsubscribe Page webhooks")
        if response.json().get("success") is not True:
            raise MetaAPIError("unsubscribe Page webhooks")
