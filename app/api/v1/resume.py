from fastapi import APIRouter

router = APIRouter(prefix="/resume", tags=["resume"])


@router.get("/")
def root():
    return {"service": "cv-builder"}
