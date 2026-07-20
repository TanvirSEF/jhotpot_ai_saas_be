"""
Resume & CV Builder API — Phase B1 (Module B)

Endpoints:
  POST   /api/v1/resume/               – Create base resume profile
  GET    /api/v1/resume/               – List user's resumes
  GET    /api/v1/resume/{resume_id}    – Get single resume details
  PUT    /api/v1/resume/{resume_id}    – Update base resume data
  DELETE /api/v1/resume/{resume_id}    – Delete resume
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import Resume, User
from app.schemas.resume import (
    OptimizeRequest,
    OptimizeResponse,
    ResumeCreate,
    ResumeOut,
    ResumeUpdate,
)
from app.services.resume_optimizer import optimize_resume_against_jd

router = APIRouter(prefix="/resume", tags=["resume"])


# ──────────────────────────────────────────────────────────────────────────────
# Helper
# ──────────────────────────────────────────────────────────────────────────────

async def _get_user_resume(
    resume_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Resume:
    """Fetch resume by ID and assert current_user ownership. Raises 404/403."""
    result = await db.execute(
        select(Resume).where(Resume.id == resume_id, Resume.user_id == current_user.id)
    )
    resume = result.scalar_one_or_none()
    if not resume:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Resume not found.",
        )
    return resume


# ──────────────────────────────────────────────────────────────────────────────
# Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "",
    response_model=ResumeOut,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new base resume",
)
async def create_resume(
    body: ResumeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    """
    Create a new raw resume record with structured section inputs
    (personal_info, work_experiences, education, skills, certifications, projects).
    """
    resume = Resume(
        user_id=current_user.id,
        title=body.title,
        raw_json_data=body.raw_data.model_dump(),
        ats_score=0,
    )
    db.add(resume)
    await db.commit()
    await db.refresh(resume)
    return resume


@router.get(
    "",
    response_model=list[ResumeOut],
    summary="List current user's resumes",
)
async def list_resumes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResumeOut]:
    """Retrieve all resumes owned by the authenticated user."""
    result = await db.execute(
        select(Resume)
        .where(Resume.user_id == current_user.id)
        .order_by(Resume.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get(
    "/{resume_id}",
    response_model=ResumeOut,
    summary="Get single resume by ID",
)
async def get_resume(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    """Get complete details of a specific resume."""
    return await _get_user_resume(resume_id, current_user, db)


@router.put(
    "/{resume_id}",
    response_model=ResumeOut,
    summary="Update raw resume profile data",
)
async def update_resume(
    resume_id: uuid.UUID,
    body: ResumeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    """Update title or raw content sections of an existing resume."""
    resume = await _get_user_resume(resume_id, current_user, db)

    if body.title is not None:
        resume.title = body.title
    if body.raw_data is not None:
        resume.raw_json_data = body.raw_data.model_dump()

    await db.commit()
    await db.refresh(resume)
    return resume


@router.delete(
    "/{resume_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a resume",
)
async def delete_resume(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    """Permanently delete a resume record."""
    resume = await _get_user_resume(resume_id, current_user, db)
    await db.delete(resume)
    await db.commit()


@router.post(
    "/{resume_id}/optimize",
    response_model=OptimizeResponse,
    summary="Optimize resume against target Job Description",
)
async def optimize_resume(
    resume_id: uuid.UUID,
    body: OptimizeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OptimizeResponse:
    """
    Accept target Job Description, trigger LLM keyword matching & ATS scoring pipeline,
    update DB `optimized_json_data` and `ats_score`, and return optimization breakdown.
    """
    resume = await _get_user_resume(resume_id, current_user, db)

    result = await optimize_resume_against_jd(
        raw_resume=resume.raw_json_data,
        target_jd=body.target_job_description,
    )

    resume.ats_score = result["ats_score"]
    resume.optimized_json_data = result["optimized_resume_content"]

    await db.commit()
    await db.refresh(resume)

    return OptimizeResponse(
        resume_id=resume.id,
        ats_score=result["ats_score"],
        keyword_analysis=result["keyword_analysis"],
        optimized_json_data=result["optimized_resume_content"],
    )


@router.get(
    "/{resume_id}/download",
    summary="Download ATS-friendly PDF resume",
)
async def download_resume_pdf(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Compile and stream ATS-friendly A4 PDF binary (`application/pdf`).
    Uses optimized data if available, otherwise falls back to raw data.
    """
    from fastapi import Response
    from app.services.pdf_generator import generate_resume_pdf

    resume = await _get_user_resume(resume_id, current_user, db)
    data = resume.optimized_json_data or resume.raw_json_data

    pdf_bytes = generate_resume_pdf(data)

    clean_filename = f"resume_{resume.title.lower().replace(' ', '_')}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{clean_filename}"'
        },
    )


