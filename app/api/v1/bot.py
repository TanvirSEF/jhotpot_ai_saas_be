from fastapi import APIRouter

router = APIRouter(prefix="/bot", tags=["bot"])


@router.get("/")
def bot_status():
    return {"service": "facebook-bot", "status": "offline"}


@router.get("/webhook")
def webhook():
    return {"detail": "not implemented"}
