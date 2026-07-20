"""
Bot status endpoint.
Webhook ingestion has moved to /api/v1/fb/webhook (Phase A3).
"""

from fastapi import APIRouter

router = APIRouter(prefix="/bot", tags=["bot"])


@router.get("/", summary="Bot service status")
def bot_status() -> dict:
    """Quick health indicator for the Facebook Bot service."""
    return {"service": "facebook-bot", "status": "active", "webhook": "/api/v1/fb/webhook"}
