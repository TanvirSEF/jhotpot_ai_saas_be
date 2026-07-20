"""
Facebook Integration API — Phase A2 + A3

── Phase A2 (OAuth + Page Management) ─────────────────────────────────────────
  GET  /api/v1/fb/connect                  → Returns OAuth URL for frontend redirect
  GET  /api/v1/fb/callback                 → OAuth callback (public, called by Meta)
  GET  /api/v1/fb/pages                    → List connected pages for authenticated user
  GET  /api/v1/fb/pages/{page_id}          → Single page details
  PATCH /api/v1/fb/pages/{page_id}/toggle  → Toggle bot active/inactive
  DELETE /api/v1/fb/pages/{page_id}        → Disconnect (remove) page
  GET  /api/v1/fb/pages/{page_id}/health   → Token validity & scope check

── Phase A3 (Webhook Ingestion) ───────────────────────────────────────────────
  GET  /api/v1/fb/webhook                  → Meta hub challenge verification (public)
  POST /api/v1/fb/webhook                  → Webhook event ingestion (public)

Security:
  OAuth:    Short-lived JWT `state` parameter prevents CSRF on the callback.
  Webhooks: HMAC-SHA256 signature verified against X-Hub-Signature-256 header
            using META_APP_SECRET before any payload processing.
  Tokens:   All Page Access Tokens are Fernet-encrypted at rest.

Performance:
  POST /webhook returns HTTP 200 in < 250 ms by immediately dispatching to
  Celery and returning before any AI/DB heavy-lifting begins.
"""

import hashlib
import hmac
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import decrypt_token, encrypt_token
from app.core.security_store import (
    SecurityStoreUnavailable,
    consume_oauth_state,
    register_oauth_state,
)
from app.db.session import get_db
from app.models import FbPage, Organization, User
from app.services.meta import (
    REQUIRED_PAGE_SUBSCRIPTIONS,
    REQUIRED_PAGE_TOKEN_SCOPES,
    MetaAPIError,
    build_oauth_url,
    debug_token,
    evaluate_token_health,
    exchange_code_for_user_token,
    get_managed_pages,
    get_page_subscription,
    subscribe_page_webhooks,
    unsubscribe_page_webhooks,
    upgrade_to_long_lived_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fb", tags=["facebook"])

# ── State JWT helpers (CSRF protection) ───────────────────────────────────────

_STATE_EXPIRE_MINUTES = 10
_STATE_ALGORITHM = "HS256"


def _create_state_token(org_id: uuid.UUID, user_id: uuid.UUID) -> tuple[str, str]:
    """Create a signed OAuth state and return it with its one-time identifier."""
    now = datetime.now(timezone.utc)
    exp = now + timedelta(minutes=_STATE_EXPIRE_MINUTES)
    state_id = str(uuid.uuid4())
    payload = {
        "org_id": str(org_id),
        "user_id": str(user_id),
        "iat": now,
        "nbf": now,
        "exp": exp,
        "iss": settings.JWT_ISSUER,
        "aud": f"{settings.JWT_AUDIENCE}:meta-oauth",
        "jti": state_id,
        "token_type": "oauth_state",
    }
    token = jwt.encode(payload, settings.SECRET_KEY, algorithm=_STATE_ALGORITHM)
    return token, state_id


def _decode_state_token(state: str) -> tuple[uuid.UUID, uuid.UUID, str]:
    """
    Decode and verify the OAuth state JWT.
    Returns (org_id, user_id, state_id) or raises HTTPException on failure.
    """
    try:
        payload = jwt.decode(
            state,
            settings.SECRET_KEY,
            algorithms=[_STATE_ALGORITHM],
            audience=f"{settings.JWT_AUDIENCE}:meta-oauth",
            issuer=settings.JWT_ISSUER,
            options={
                "require": [
                    "org_id",
                    "user_id",
                    "iat",
                    "nbf",
                    "exp",
                    "iss",
                    "aud",
                    "jti",
                    "token_type",
                ]
            },
        )
        if payload.get("token_type") != "oauth_state":
            raise jwt.InvalidTokenError("Token is not an OAuth state")
        return (
            uuid.UUID(payload["org_id"]),
            uuid.UUID(payload["user_id"]),
            payload["jti"],
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "OAuth state token has expired. Please try connecting again.",
        ) from None
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        logger.warning("Invalid OAuth state token: %s", exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid OAuth state. Possible CSRF attempt.",
        ) from None


# ── Pydantic Schemas ───────────────────────────────────────────────────────────

class ConnectResponse(BaseModel):
    oauth_url: str
    message: str = (
        "Redirect the user to oauth_url to begin Facebook Page authorization."
    )


class PageOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    page_id: str
    page_name: str | None
    is_bot_active: bool
    connection_status: str
    subscription_status: str
    token_status: str
    subscribed_fields: list[str]
    token_expires_at: datetime | None
    data_access_expires_at: datetime | None
    last_token_check_at: datetime | None
    last_subscription_attempt_at: datetime | None
    last_error_code: str | None
    disconnected_at: datetime | None
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class ToggleResponse(BaseModel):
    page_id: str
    is_bot_active: bool
    message: str


class PageHealthOut(BaseModel):
    page_id: str
    page_name: str | None
    connection_status: str
    subscription_status: str
    token_status: str
    subscribed_fields: list[str]
    required_fields: list[str]
    missing_scopes: list[str]
    token_expires_at: datetime | None
    data_access_expires_at: datetime | None
    checked_at: datetime


class SubscriptionResponse(BaseModel):
    page_id: str
    subscription_status: str
    subscribed_fields: list[str]


class PageTransferRequest(BaseModel):
    target_org_id: uuid.UUID


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_page_for_user(
    db: AsyncSession,
    page_record_id: uuid.UUID,
    current_user: User,
) -> FbPage:
    """
    Fetch an fb_pages row and assert it belongs to one of the current
    user's organizations. Raises 404 / 403 accordingly.
    """
    result = await db.execute(
        select(FbPage)
        .join(Organization, FbPage.org_id == Organization.id)
        .where(
            FbPage.id == page_record_id,
            Organization.user_id == current_user.id,
        )
    )
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Facebook Page not found.")
    return page


def _require_page_token(page: FbPage) -> str:
    if not page.encrypted_access_token:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Facebook Page must be reconnected before this operation.",
        )
    try:
        return decrypt_token(page.encrypted_access_token)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Stored Facebook credential is unavailable; reconnect the Page.",
        ) from exc


def _apply_token_health(page: FbPage, token_info: dict, *, checked_at: datetime) -> list[str]:
    health = evaluate_token_health(token_info, now=checked_at)
    page.token_status = health.status
    page.token_expires_at = health.expires_at
    page.data_access_expires_at = health.data_access_expires_at
    page.last_token_check_at = checked_at
    if health.status != "valid":
        page.connection_status = "needs_reauth"
        page.is_bot_active = False
    return health.missing_scopes


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/connect", response_model=ConnectResponse)
async def connect_facebook_page(
    org_id: uuid.UUID = Query(..., description="Organization to link the Facebook Page to."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectResponse:
    """
    Step 1 of OAuth: verify the org belongs to the user, generate a
    CSRF-protected state token, and return the Facebook authorization URL.

    The frontend should redirect the merchant's browser to `oauth_url`.
    """
    # Verify org ownership
    result = await db.execute(
        select(Organization).where(
            Organization.id == org_id,
            Organization.user_id == current_user.id,
        )
    )
    if not result.scalar_one_or_none():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")

    state, state_id = _create_state_token(org_id, current_user.id)
    try:
        await register_oauth_state(
            state_id,
            ttl_seconds=_STATE_EXPIRE_MINUTES * 60,
        )
    except SecurityStoreUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Facebook connection is temporarily unavailable.",
        ) from exc
    oauth_url = build_oauth_url(state)

    logger.info(
        "OAuth URL generated for user=%s org=%s", current_user.id, org_id
    )
    return ConnectResponse(oauth_url=oauth_url)


@router.get("/callback")
async def facebook_oauth_callback(
    code: str = Query(..., description="Authorization code from Meta."),
    state: str = Query(..., description="CSRF state token."),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """
    Step 2 of OAuth: called directly by Meta after the user grants permissions.

    Pipeline:
      1. Verify CSRF state JWT → extract org_id + user_id
      2. Exchange code → short-lived user token
      3. Upgrade → long-lived user token (~60 days)
      4. Fetch list of managed Pages + page-level tokens
      5. Fernet-encrypt each Page Access Token
      6. Upsert fb_pages rows (on conflict update token + name)
      7. Redirect merchant back to frontend dashboard

    This endpoint is PUBLIC (no JWT auth) because Meta sends the browser
    here directly — the user's session JWT is not available at this point.
    CSRF protection is handled by the signed `state` parameter.
    """
    # ── 1. Verify state ───────────────────────────────────────────────────────
    org_id, user_id, state_id = _decode_state_token(state)
    try:
        state_is_valid = await consume_oauth_state(state_id)
    except SecurityStoreUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Facebook connection is temporarily unavailable.",
        ) from exc
    if not state_is_valid:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "OAuth state has already been used or is no longer valid.",
        )

    # ── 2. Verify org still exists and belongs to user ────────────────────────
    result = await db.execute(
        select(Organization).where(
            Organization.id == org_id,
            Organization.user_id == user_id,
        )
    )
    if not result.scalar_one_or_none():
        logger.error("OAuth callback: org %s not found for user %s", org_id, user_id)
        redirect_url = (
            f"{settings.FRONTEND_URL}/dashboard/pages"
            "?status=error&message=organization_not_found"
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    # ── 3 & 4. Token exchange pipeline ───────────────────────────────────────
    try:
        short_lived = await exchange_code_for_user_token(code)
        long_lived = await upgrade_to_long_lived_token(short_lived)
        pages = await get_managed_pages(long_lived)
    except HTTPException as exc:
        logger.error("OAuth token exchange failed: %s", exc.detail)
        redirect_url = (
            f"{settings.FRONTEND_URL}/dashboard/pages"
            f"?status=error&message=token_exchange_failed"
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    if not pages:
        logger.warning("No manageable pages found for user %s", user_id)
        redirect_url = (
            f"{settings.FRONTEND_URL}/dashboard/pages"
            "?status=warning&message=no_pages_found"
        )
        return RedirectResponse(url=redirect_url, status_code=302)

    # ── 5 & 6. Encrypt and upsert each page ──────────────────────────────────
    connected_count = 0
    refreshed_count = 0
    conflict_count = 0
    subscribed_count = 0
    for page_data in pages:
        encrypted_token = encrypt_token(page_data.access_token)

        existing_result = await db.execute(
            select(FbPage).where(FbPage.page_id == page_data.page_id)
        )
        existing = existing_result.scalar_one_or_none()

        if existing is not None and existing.org_id != org_id:
            conflict_count += 1
            logger.warning(
                "Page ownership conflict page=%s requested_org=%s",
                page_data.page_id,
                org_id,
            )
            continue

        if existing is None:
            page = FbPage(
                org_id=org_id,
                page_id=page_data.page_id,
                page_name=page_data.page_name,
                encrypted_access_token=encrypted_token,
                is_bot_active=False,
                connection_status="connected",
                subscription_status="pending",
                token_status="unknown",
                subscribed_fields=[],
            )
            db.add(page)
            connected_count += 1
        else:
            page = existing
            page.encrypted_access_token = encrypted_token
            page.page_name = page_data.page_name
            page.connection_status = "connected"
            page.subscription_status = "pending"
            page.token_status = "unknown"
            page.subscribed_fields = []
            page.disconnected_at = None
            refreshed_count += 1

        checked_at = datetime.now(timezone.utc)
        try:
            token_info = await debug_token(page_data.access_token)
            _apply_token_health(page, token_info, checked_at=checked_at)
            if page.token_status == "valid":
                page.last_subscription_attempt_at = checked_at
                subscription = await subscribe_page_webhooks(
                    page.page_id,
                    page_data.access_token,
                )
                page.subscription_status = "subscribed"
                page.subscribed_fields = subscription.fields
                page.is_bot_active = True
                page.last_error_code = None
                subscribed_count += 1
            else:
                page.subscription_status = "failed"
                page.last_error_code = page.token_status
        except MetaAPIError as exc:
            page.last_error_code = f"meta_{exc.meta_code or 'request_failed'}"[:100]
            page.subscription_status = "failed"
            page.is_bot_active = False
            if exc.meta_code == 190:
                page.token_status = "invalid"
                page.connection_status = "needs_reauth"

        logger.info(
            "Page lifecycle updated page=%s connection=%s subscription=%s token=%s",
            page.page_id,
            page.connection_status,
            page.subscription_status,
            page.token_status,
        )

    await db.commit()

    logger.info(
        "OAuth complete — org=%s pages_connected=%d pages_refreshed=%d",
        org_id,
        connected_count,
        refreshed_count,
    )

    # ── 7. Redirect to frontend ───────────────────────────────────────────────
    callback_status = (
        "partial"
        if conflict_count
        or subscribed_count < connected_count + refreshed_count
        else "connected"
    )
    redirect_url = (
        f"{settings.FRONTEND_URL}/dashboard/pages"
        f"?status={callback_status}"
        f"&pages={connected_count + refreshed_count}"
        f"&subscribed={subscribed_count}&conflicts={conflict_count}"
    )
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/pages", response_model=list[PageOut])
async def list_connected_pages(
    org_id: uuid.UUID | None = Query(None, description="Filter by a specific organization."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FbPage]:
    """
    List all Facebook Pages connected to the authenticated user's organizations.
    Optionally filter by a specific org_id.
    """
    query = (
        select(FbPage)
        .join(Organization, FbPage.org_id == Organization.id)
        .where(Organization.user_id == current_user.id)
        .order_by(FbPage.created_at.desc())
    )
    if org_id:
        query = query.where(FbPage.org_id == org_id)

    result = await db.execute(query)
    return list(result.scalars().all())


@router.get("/pages/{page_record_id}", response_model=PageOut)
async def get_connected_page(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FbPage:
    """Get details of a single connected Facebook Page."""
    return await _get_page_for_user(db, page_record_id, current_user)


@router.patch("/pages/{page_record_id}/toggle", response_model=ToggleResponse)
async def toggle_bot_active(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ToggleResponse:
    """
    Flip the is_bot_active flag for a connected page.
    When False the Celery worker skips AI processing for this page's webhooks.
    """
    page = await _get_page_for_user(db, page_record_id, current_user)
    activating = not page.is_bot_active
    if activating and (
        page.connection_status != "connected"
        or page.subscription_status != "subscribed"
        or page.token_status != "valid"
    ):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Page health must be valid and subscribed before activating the bot.",
        )
    page.is_bot_active = activating
    await db.commit()
    await db.refresh(page)

    state_label = "activated" if page.is_bot_active else "paused"
    logger.info("Bot %s for page %s", state_label, page.page_id)

    return ToggleResponse(
        page_id=page.page_id,
        is_bot_active=page.is_bot_active,
        message=f"Bot {state_label} for page '{page.page_name}'.",
    )


@router.delete("/pages/{page_record_id}", status_code=status.HTTP_204_NO_CONTENT)
async def disconnect_page(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Disconnect a Facebook Page, remove its remote webhook subscription, and
    erase the encrypted token while retaining lifecycle history.
    """
    page = await _get_page_for_user(db, page_record_id, current_user)
    if page.connection_status == "disconnected":
        return None
    unsubscribe_error: str | None = None
    if page.encrypted_access_token:
        try:
            plain_token = decrypt_token(page.encrypted_access_token)
        except Exception:
            plain_token = None
            unsubscribe_error = "credential_unavailable"
        if plain_token:
            try:
                await unsubscribe_page_webhooks(page.page_id, plain_token)
            except MetaAPIError as exc:
                if exc.meta_code != 190:
                    unsubscribe_error = (
                        f"meta_unsubscribe_{exc.meta_code or 'request_failed'}"[:100]
                    )
                    logger.warning(
                        "Remote Page unsubscribe failed record=%s code=%s; "
                        "continuing local disconnect",
                        page_record_id,
                        exc.meta_code,
                    )

    page.encrypted_access_token = None
    page.is_bot_active = False
    page.connection_status = "disconnected"
    page.subscription_status = "failed" if unsubscribe_error else "unsubscribed"
    page.token_status = "missing"
    page.subscribed_fields = []
    page.disconnected_at = datetime.now(timezone.utc)
    page.last_error_code = unsubscribe_error
    await db.commit()
    logger.info("Disconnected page record=%s external_page=%s", page_record_id, page.page_id)


@router.patch("/pages/{page_record_id}/transfer", response_model=PageOut)
async def transfer_disconnected_page(
    page_record_id: uuid.UUID,
    body: PageTransferRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FbPage:
    page = await _get_page_for_user(db, page_record_id, current_user)
    if page.connection_status != "disconnected":
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Disconnect the Page before transferring it to another organization.",
        )
    target = await db.execute(
        select(Organization.id).where(
            Organization.id == body.target_org_id,
            Organization.user_id == current_user.id,
        )
    )
    if target.scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Target organization not found.")
    page.org_id = body.target_org_id
    await db.commit()
    await db.refresh(page)
    return page


@router.get("/pages/{page_record_id}/health", response_model=PageHealthOut)
async def check_page_token_health(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PageHealthOut:
    """
    Diagnostic: decode the stored page token and check its validity + scopes
    via Meta's debug_token endpoint.

    Returns a sanitized lifecycle assessment and never exposes token payloads.
    """
    page = await _get_page_for_user(db, page_record_id, current_user)
    checked_at = datetime.now(timezone.utc)
    try:
        plain_token = _require_page_token(page)
    except HTTPException:
        page.connection_status = "needs_reauth"
        page.token_status = "missing"
        page.subscription_status = "unsubscribed"
        page.is_bot_active = False
        page.last_token_check_at = checked_at
        await db.commit()
        raise

    try:
        token_info = await debug_token(plain_token)
        missing_scopes = _apply_token_health(page, token_info, checked_at=checked_at)
    except MetaAPIError as exc:
        page.last_token_check_at = checked_at
        page.last_error_code = f"meta_{exc.meta_code or 'request_failed'}"[:100]
        if exc.meta_code == 190:
            page.connection_status = "needs_reauth"
            page.token_status = "invalid"
            page.is_bot_active = False
            await db.commit()
            return PageHealthOut(
                page_id=page.page_id,
                page_name=page.page_name,
                connection_status=page.connection_status,
                subscription_status=page.subscription_status,
                token_status=page.token_status,
                subscribed_fields=page.subscribed_fields,
                required_fields=list(REQUIRED_PAGE_SUBSCRIPTIONS),
                missing_scopes=list(REQUIRED_PAGE_TOKEN_SCOPES),
                token_expires_at=page.token_expires_at,
                data_access_expires_at=page.data_access_expires_at,
                checked_at=checked_at,
            )
        await db.commit()
        raise

    if page.token_status == "valid":
        try:
            subscription = await get_page_subscription(page.page_id, plain_token)
        except MetaAPIError as exc:
            page.subscription_status = "failed"
            page.is_bot_active = False
            page.last_error_code = f"meta_{exc.meta_code or 'request_failed'}"[:100]
            await db.commit()
            raise
        missing_fields = set(REQUIRED_PAGE_SUBSCRIPTIONS) - set(subscription.fields)
        page.subscribed_fields = subscription.fields
        page.subscription_status = (
            "subscribed" if subscription.subscribed and not missing_fields else "failed"
        )
        if page.subscription_status != "subscribed":
            page.is_bot_active = False
            page.last_error_code = "subscription_missing_fields"
        else:
            page.last_error_code = None
    else:
        page.subscription_status = "failed"
        page.last_error_code = page.token_status
    page.connection_status = "connected" if page.token_status == "valid" else "needs_reauth"
    await db.commit()

    return PageHealthOut(
        page_id=page.page_id,
        page_name=page.page_name,
        connection_status=page.connection_status,
        subscription_status=page.subscription_status,
        token_status=page.token_status,
        subscribed_fields=page.subscribed_fields,
        required_fields=list(REQUIRED_PAGE_SUBSCRIPTIONS),
        missing_scopes=missing_scopes,
        token_expires_at=page.token_expires_at,
        data_access_expires_at=page.data_access_expires_at,
        checked_at=checked_at,
    )


@router.post("/pages/{page_record_id}/subscribe", response_model=SubscriptionResponse)
async def repair_page_subscription(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionResponse:
    page = await _get_page_for_user(db, page_record_id, current_user)
    plain_token = _require_page_token(page)
    checked_at = datetime.now(timezone.utc)
    try:
        token_info = await debug_token(plain_token)
    except MetaAPIError as exc:
        page.last_token_check_at = checked_at
        page.last_error_code = f"meta_{exc.meta_code or 'request_failed'}"[:100]
        page.subscription_status = "failed"
        page.is_bot_active = False
        if exc.meta_code == 190:
            page.connection_status = "needs_reauth"
            page.token_status = "invalid"
        await db.commit()
        raise
    _apply_token_health(page, token_info, checked_at=checked_at)
    if page.token_status != "valid":
        page.subscription_status = "failed"
        page.last_error_code = page.token_status
        await db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Page token is not healthy; reconnect before subscribing.",
        )

    page.last_subscription_attempt_at = checked_at
    try:
        subscription = await subscribe_page_webhooks(page.page_id, plain_token)
    except MetaAPIError as exc:
        page.subscription_status = "failed"
        page.is_bot_active = False
        page.last_error_code = f"meta_{exc.meta_code or 'request_failed'}"[:100]
        await db.commit()
        raise

    page.connection_status = "connected"
    page.subscription_status = "subscribed"
    page.subscribed_fields = subscription.fields
    page.last_error_code = None
    await db.commit()
    return SubscriptionResponse(
        page_id=page.page_id,
        subscription_status=page.subscription_status,
        subscribed_fields=page.subscribed_fields,
    )


@router.get("/pages/{page_record_id}/reconnect", response_model=ConnectResponse)
async def reconnect_page(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ConnectResponse:
    page = await _get_page_for_user(db, page_record_id, current_user)
    state, state_id = _create_state_token(page.org_id, current_user.id)
    try:
        await register_oauth_state(state_id, ttl_seconds=_STATE_EXPIRE_MINUTES * 60)
    except SecurityStoreUnavailable as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Facebook reconnection is temporarily unavailable.",
        ) from exc
    return ConnectResponse(oauth_url=build_oauth_url(state))


# ══════════════════════════════════════════════════════════════════════════════
# Phase A3 — Webhook Ingestion Endpoints
# ══════════════════════════════════════════════════════════════════════════════

@router.get("/webhook")
async def webhook_verify(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> int:
    """
    Meta Webhook Verification Handshake (Phase A3).

    When a developer registers a webhook callback URL in the Meta App
    Dashboard, Meta sends a GET request with these three query parameters
    to confirm that the endpoint is live and owned by the app developer.

    Flow:
      1. Meta sends: hub.mode="subscribe", hub.verify_token=<our_secret>,
                     hub.challenge=<random_int_string>
      2. We verify hub.mode and hub.verify_token match our config.
      3. We echo back hub.challenge as a plain integer → Meta confirms.
      4. Any mismatch → 403 Forbidden (endpoint rejected).

    This endpoint is PUBLIC (no JWT auth).
    """
    if hub_mode == "subscribe" and hub_verify_token == settings.META_VERIFY_TOKEN:
        logger.info("Webhook verification handshake successful.")
        # Meta expects the challenge echoed back as a plain integer
        return int(hub_challenge)

    logger.warning(
        "Webhook verification FAILED — mode=%s token_match=%s",
        hub_mode,
        hub_verify_token == settings.META_VERIFY_TOKEN,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Webhook verification failed: invalid hub.verify_token.",
    )


@router.post("/webhook", status_code=status.HTTP_200_OK)
async def webhook_ingest(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Meta Webhook Event Ingestion (Phase A3).

    Design contract:
      - Must return HTTP 200 within 20 s (we target < 250 ms).
      - Any failure in AI processing must NOT delay this response.
      - Meta retries if it receives non-200 or no response within the window.

    Pipeline:
      1. Read raw request body bytes (required for HMAC computation).
      2. Verify X-Hub-Signature-256 header using HMAC-SHA256(APP_SECRET, body).
         → Reject with 403 if invalid (prevents spoofed webhook calls).
         → Use hmac.compare_digest() to prevent timing-attack leakage.
      3. Parse body as JSON.
      4. Pass payload to webhook_parser to produce typed event list.
      5. Commit each provider event ID to the durable PostgreSQL inbox.
      6. Dispatch newly accepted inbox IDs as independent Celery tasks.
      7. Return {"status": "ok"}; broker failures remain recoverable.

    Phase A4 will implement the full RAG pipeline inside the Celery worker.
    This endpoint only enqueues — it never awaits AI results.
    """
    from app.services.webhook_inbox import (
        mark_webhook_queued,
        persist_webhook_events,
    )
    from app.services.webhook_parser import parse_webhook_payload
    from app.worker.reliability import correlation_headers
    from app.worker.tasks import process_fb_webhook

    # ── 1. Read raw body (must happen before any framework parsing) ───────────
    raw_body: bytes = await request.body()

    # ── 2. HMAC-SHA256 Signature Verification ─────────────────────────────────
    signature_header = request.headers.get("X-Hub-Signature-256", "")

    if not signature_header.startswith("sha256="):
        logger.warning("Webhook received without X-Hub-Signature-256 header.")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Missing webhook signature.",
        )

    received_digest = signature_header[len("sha256="):]
    expected_digest = hmac.new(
        settings.META_APP_SECRET.encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()

    # Constant-time comparison prevents timing-attack side-channel leakage
    if not hmac.compare_digest(received_digest, expected_digest):
        logger.warning(
            "Webhook HMAC verification failed — possible spoofed request."
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid webhook signature.",
        )

    # ── 3. Parse JSON payload ─────────────────────────────────────────────────
    try:
        payload: dict = json.loads(raw_body)
    except json.JSONDecodeError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed JSON body.",
        ) from None

    # ── 4. Classify events ────────────────────────────────────────────────────
    events = parse_webhook_payload(payload)

    # ── 5. Dispatch each event as independent Celery task ─────────────────────
    # Serialise typed dataclass → dict for Celery JSON serializer
    inbox = await persist_webhook_events(
        db,
        events,
        request_id=getattr(request.state, "request_id", None),
    )

    # The inbox commit is the durability boundary. If Redis is unavailable,
    # recovery will publish these accepted rows after the broker returns.
    dispatched = 0
    for event in inbox.accepted:
        task_id = str(uuid.uuid4())
        try:
            process_fb_webhook.apply_async(
                args=(str(event.id),),
                task_id=task_id,
                headers=correlation_headers(request),
            )
        except Exception as exc:
            logger.warning(
                "Webhook publish deferred inbox_event=%s error=%s",
                event.id,
                type(exc).__name__,
            )
            continue
        await mark_webhook_queued(db, event.id, task_id)
        dispatched += 1
    await db.commit()

    logger.info(
        "Webhook ingested accepted=%d duplicate=%d unregistered=%d queued=%d",
        len(inbox.accepted),
        inbox.duplicates,
        inbox.unregistered,
        dispatched,
    )

    # ── 6. Immediate 200 OK ───────────────────────────────────────────────────
    # This response goes back to Meta in < 250 ms regardless of how long
    # the Celery RAG pipeline takes (Phase A4).
    return {
        "status": "ok",
        "events_accepted": len(inbox.accepted),
        "events_duplicate": inbox.duplicates,
        "events_unregistered": inbox.unregistered,
        "events_queued": dispatched,
    }
