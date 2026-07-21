


from fastapi import APIRouter

router = APIRouter(prefix="/bot", tags=["bot"])


@router.get("/", summary="Bot service status")
def bot_status() -> dict:

    return {"service": "facebook-bot", "status": "active", "webhook": "/api/v1/fb/webhook"}
