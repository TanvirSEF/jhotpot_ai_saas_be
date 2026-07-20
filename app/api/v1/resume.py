"""Tenant-owned resume optimization and durable PDF export API."""

import logging
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import Resume, ResumeExport, ResumeExportState, User
from app.schemas.resume import (
    OptimizeRequest,
    OptimizeResponse,
    ResumeCreate,
    ResumeExportOut,
    ResumeOut,
    ResumeUpdate,
)
from app.services.export_storage import ExportStorageError, get_export_storage
from app.services.resume_optimizer import (
    ResumeOptimizationError,
    optimize_resume_against_jd,
)
from app.worker.reliability import correlation_headers

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/resume", tags=["resume"])


async def _get_user_resume(
    resume_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Resume:
    result = await db.execute(
        select(Resume).where(
            Resume.id == resume_id,
            Resume.user_id == current_user.id,
        )
    )
    resume = result.scalar_one_or_none()
    if resume is None:
        raise HTTPException(status_code=404, detail="Resume not found.")
    return resume


async def _get_user_export(
    resume_id: uuid.UUID,
    export_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> ResumeExport:
    result = await db.execute(
        select(ResumeExport).where(
            ResumeExport.id == export_id,
            ResumeExport.resume_id == resume_id,
            ResumeExport.user_id == current_user.id,
        )
    )
    export = result.scalar_one_or_none()
    if export is None:
        raise HTTPException(status_code=404, detail="Resume export not found.")
    return export


def _export_filename(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", title.casefold()).strip("_")[:220]
    return f"resume_{slug or 'export'}.pdf"


def _file_response(export: ResumeExport) -> FileResponse:
    if export.state != ResumeExportState.READY.value or not export.storage_key:
        raise HTTPException(status_code=409, detail="Resume export is not ready.")
    try:
        path = get_export_storage().path(export.storage_key)
    except ExportStorageError as exc:
        raise HTTPException(status_code=503, detail="Resume export file is unavailable.") from exc
    return FileResponse(
        path=path,
        media_type=export.content_type,
        filename=export.filename,
    )


@router.post("", response_model=ResumeOut, status_code=201)
async def create_resume(
    body: ResumeCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    resume = Resume(
        user_id=current_user.id,
        title=body.title,
        raw_json_data=body.raw_data.model_dump(mode="json"),
        ats_score=0,
    )
    db.add(resume)
    await db.commit()
    await db.refresh(resume)
    return resume


@router.get("", response_model=list[ResumeOut])
async def list_resumes(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ResumeOut]:
    result = await db.execute(
        select(Resume)
        .where(Resume.user_id == current_user.id)
        .order_by(Resume.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{resume_id}", response_model=ResumeOut)
async def get_resume(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    return await _get_user_resume(resume_id, current_user, db)


@router.put("/{resume_id}", response_model=ResumeOut)
async def update_resume(
    resume_id: uuid.UUID,
    body: ResumeUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeOut:
    resume = await _get_user_resume(resume_id, current_user, db)
    if body.title is not None:
        resume.title = body.title
    if body.raw_data is not None:
        resume.raw_json_data = body.raw_data.model_dump(mode="json")
        resume.optimized_json_data = None
        resume.ats_score = 0
    await db.commit()
    await db.refresh(resume)
    return resume


@router.delete("/{resume_id}", status_code=204)
async def delete_resume(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    resume = await _get_user_resume(resume_id, current_user, db)
    result = await db.execute(
        select(ResumeExport.storage_key).where(
            ResumeExport.resume_id == resume.id,
            ResumeExport.storage_key.is_not(None),
        )
    )
    storage = get_export_storage()
    for key in result.scalars():
        try:
            storage.delete(key)
        except ExportStorageError:
            logger.warning("Could not remove stored resume export during deletion.")
    await db.delete(resume)
    await db.commit()


@router.post("/{resume_id}/optimize", response_model=OptimizeResponse)
async def optimize_resume(
    resume_id: uuid.UUID,
    body: OptimizeRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> OptimizeResponse:
    resume = await _get_user_resume(resume_id, current_user, db)
    try:
        result = await optimize_resume_against_jd(
            raw_resume=resume.raw_json_data,
            target_jd=body.target_job_description,
        )
    except ResumeOptimizationError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    optimized = result.optimized_resume_content.model_dump(mode="json")
    resume.ats_score = result.ats_score
    resume.optimized_json_data = optimized
    await db.commit()
    await db.refresh(resume)
    return OptimizeResponse(
        resume_id=resume.id,
        ats_score=result.ats_score,
        keyword_analysis=result.keyword_analysis,
        optimized_json_data=result.optimized_resume_content,
    )


@router.post(
    "/{resume_id}/exports",
    response_model=ResumeExportOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_resume_export(
    resume_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeExportOut:
    resume = await _get_user_resume(resume_id, current_user, db)
    task_id = str(uuid.uuid4())
    source_kind = "optimized" if resume.optimized_json_data is not None else "raw"
    export = ResumeExport(
        resume_id=resume.id,
        user_id=current_user.id,
        state=ResumeExportState.PENDING.value,
        task_id=task_id,
        source_json_data=resume.optimized_json_data or resume.raw_json_data,
        source_kind=source_kind,
        filename=_export_filename(resume.title),
    )
    db.add(export)
    await db.commit()
    await db.refresh(export)

    from app.worker.tasks import export_resume_pdf

    try:
        export_resume_pdf.apply_async(
            args=(str(export.id),),
            task_id=task_id,
            headers=correlation_headers(request),
        )
    except Exception:
        logger.warning(
            "Resume export publish failed; recovery will retry export_id=%s",
            export.id,
        )
    return export


@router.get(
    "/{resume_id}/exports/{export_id}",
    response_model=ResumeExportOut,
)
async def get_resume_export(
    resume_id: uuid.UUID,
    export_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ResumeExportOut:
    return await _get_user_export(resume_id, export_id, current_user, db)


@router.get("/{resume_id}/exports/{export_id}/download")
async def download_specific_resume_export(
    resume_id: uuid.UUID,
    export_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    export = await _get_user_export(resume_id, export_id, current_user, db)
    return _file_response(export)


@router.get("/{resume_id}/download")
async def download_resume_pdf(
    resume_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Download the newest validated export; compilation never runs in the API."""
    await _get_user_resume(resume_id, current_user, db)
    result = await db.execute(
        select(ResumeExport)
        .where(
            ResumeExport.resume_id == resume_id,
            ResumeExport.user_id == current_user.id,
            ResumeExport.state == ResumeExportState.READY.value,
        )
        .order_by(ResumeExport.finished_at.desc())
        .limit(1)
    )
    export = result.scalar_one_or_none()
    if export is None:
        raise HTTPException(
            status_code=409,
            detail="No ready PDF export. Create an export job first.",
        )
    return _file_response(export)
