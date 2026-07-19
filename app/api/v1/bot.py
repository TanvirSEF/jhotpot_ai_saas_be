"""Facebook bot routes (placeholder)."""
from fastapi import APIRouter

router = APIRouter(prefix="/bot", tags=["bot"])


@router.get("/")
def bot_status():
    return {"service": "facebook-bot", "status": "offline"}


@router.get("/webhook")
def webhook_verify():
    """Stub for Meta's webhook verification step."""
    return {"detail": "webhook verification not implemented yet"}
