"""
Facebook Integration API — Phase A2 (OAuth + Page Management)

Endpoints:
  GET  /api/v1/fb/connect                  → Returns OAuth URL for frontend redirect
  GET  /api/v1/fb/callback                 → OAuth callback (public, called by Meta)
  GET  /api/v1/fb/pages                    → List connected pages for authenticated user
  GET  /api/v1/fb/pages/{page_id}          → Single page details
  PATCH /api/v1/fb/pages/{page_id}/toggle  → Toggle bot active/inactive
  DELETE /api/v1/fb/pages/{page_id}        → Disconnect (remove) page

OAuth CSRF Protection:
  The `state` parameter is a short-lived JWT signed with SECRET_KEY.
  Payload: {"org_id": "...", "user_id": "...", "exp": <unix timestamp>}
  The callback verifies the JWT before processing — this prevents
  CSRF attacks where a malicious redirect triggers page connection.

Token Storage:
  Page Access Tokens are Fernet-encrypted before being written to
  fb_pages.encrypted_access_token. The raw token never appears in
  any API response or log entry.
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import get_current_user
from app.core.security import decrypt_token, encrypt_token
from app.db.session import get_db
from app.models import FbPage, Organization, User
from app.services.meta import (
    build_oauth_url,
    debug_token,
    exchange_code_for_user_token,
    get_managed_pages,
    upgrade_to_long_lived_token,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/fb", tags=["facebook"])

# ── State JWT helpers (CSRF protection) ───────────────────────────────────────

_STATE_EXPIRE_MINUTES = 10
_STATE_ALGORITHM = "HS256"


def _create_state_token(org_id: uuid.UUID, user_id: uuid.UUID) -> str:
    """Create a short-lived JWT used as the OAuth `state` parameter."""
    exp = datetime.now(timezone.utc) + timedelta(minutes=_STATE_EXPIRE_MINUTES)
    payload = {
        "org_id": str(org_id),
        "user_id": str(user_id),
        "exp": exp,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=_STATE_ALGORITHM)


def _decode_state_token(state: str) -> tuple[uuid.UUID, uuid.UUID]:
    """
    Decode and verify the OAuth state JWT.
    Returns (org_id, user_id) or raises HTTPException on any failure.
    """
    try:
        payload = jwt.decode(
            state, settings.SECRET_KEY, algorithms=[_STATE_ALGORITHM]
        )
        return uuid.UUID(payload["org_id"]), uuid.UUID(payload["user_id"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "OAuth state token has expired. Please try connecting again.",
        )
    except (jwt.PyJWTError, KeyError, ValueError) as exc:
        logger.warning("Invalid OAuth state token: %s", exc)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Invalid OAuth state. Possible CSRF attempt.",
        )


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
    created_at: datetime

    model_config = {"from_attributes": True}


class ToggleResponse(BaseModel):
    page_id: str
    is_bot_active: bool
    message: str


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

    state = _create_state_token(org_id, current_user.id)
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
    org_id, user_id = _decode_state_token(state)

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
    for page_data in pages:
        encrypted_token = encrypt_token(page_data.access_token)

        existing_result = await db.execute(
            select(FbPage).where(
                FbPage.page_id == page_data.page_id,
                FbPage.org_id == org_id,
            )
        )
        existing = existing_result.scalar_one_or_none()

        if existing:
            # Refresh token and name on reconnect
            existing.encrypted_access_token = encrypted_token
            existing.page_name = page_data.page_name
            existing.is_bot_active = True
            logger.info("Refreshed token for existing page %s", page_data.page_id)
        else:
            new_page = FbPage(
                org_id=org_id,
                page_id=page_data.page_id,
                page_name=page_data.page_name,
                encrypted_access_token=encrypted_token,
                is_bot_active=True,
            )
            db.add(new_page)
            connected_count += 1
            logger.info("Connected new page: %s (%s)", page_data.page_name, page_data.page_id)

    await db.commit()

    logger.info(
        "OAuth complete — org=%s pages_connected=%d pages_refreshed=%d",
        org_id,
        connected_count,
        len(pages) - connected_count,
    )

    # ── 7. Redirect to frontend ───────────────────────────────────────────────
    redirect_url = (
        f"{settings.FRONTEND_URL}/dashboard/pages"
        f"?status=connected&pages={len(pages)}"
    )
    return RedirectResponse(url=redirect_url, status_code=302)


@router.get("/pages", response_model=list[PageOut])
async def list_connected_pages(
    org_id: uuid.UUID | None = Query(None, description="Filter by a specific organization."),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[PageOut]:
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
    return result.scalars().all()


@router.get("/pages/{page_record_id}", response_model=PageOut)
async def get_connected_page(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> PageOut:
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
    page.is_bot_active = not page.is_bot_active
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
    Disconnect a Facebook Page — deletes the db record and its encrypted token.
    The merchant will need to reconnect via /fb/connect to re-enable the bot.
    """
    page = await _get_page_for_user(db, page_record_id, current_user)
    page_name = page.page_name
    await db.delete(page)
    await db.commit()
    logger.info("Disconnected page '%s' (id=%s)", page_name, page_record_id)


@router.get("/pages/{page_record_id}/health")
async def check_page_token_health(
    page_record_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Diagnostic: decode the stored page token and check its validity + scopes
    via Meta's debug_token endpoint.

    Returns Meta's raw token inspection payload.
    """
    page = await _get_page_for_user(db, page_record_id, current_user)
    plain_token = decrypt_token(page.encrypted_access_token)
    token_info = await debug_token(plain_token)
    # Never return the raw token in the response
    return {
        "page_id": page.page_id,
        "page_name": page.page_name,
        "is_bot_active": page.is_bot_active,
        "token_valid": token_info.get("is_valid", False),
        "expires_at": token_info.get("expires_at"),
        "scopes": token_info.get("scopes", []),
    }
