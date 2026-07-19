"""CV / resume builder routes (placeholder)."""
from fastapi import APIRouter

router = APIRouter(prefix="/resume", tags=["resume"])


@router.get("/")
def resume_root():
    return {"service": "cv-builder"}
