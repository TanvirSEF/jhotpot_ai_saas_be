import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import EmbeddingJobState, KnowledgeEmbedding, Organization, User
from app.services.embedding_status import set_embedding_status
from app.worker.dispatch import queue_embedding_tasks
from app.worker.reliability import correlation_headers

router = APIRouter(prefix="/org", tags=["organization"])


class OrgCreate(BaseModel):
    business_name: str
    global_guidelines: str | None = None


class OrgUpdate(BaseModel):
    business_name: str | None = None
    global_guidelines: str | None = None


class OrgOut(BaseModel):
    id: uuid.UUID
    user_id: uuid.UUID
    business_name: str
    global_guidelines: str | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    org_in: OrgCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = Organization(
        user_id=current_user.id,
        business_name=org_in.business_name,
        global_guidelines=org_in.global_guidelines,
    )
    db.add(org)
    await db.flush()
    if org.global_guidelines and org.global_guidelines.strip():
        await queue_embedding_tasks(
            db,
            [(org.id, "guideline", org.id)],
            headers=correlation_headers(request),
        )
    else:
        await set_embedding_status(
            db,
            org_id=org.id,
            entity_type="guideline",
            entity_id=org.id,
            state=EmbeddingJobState.NOT_REQUIRED,
        )
        await db.commit()
    await db.refresh(org)
    return org


@router.get("", response_model=list[OrgOut])
async def get_orgs(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(Organization.user_id == current_user.id)
    )
    return result.scalars().all()


@router.get("/{org_id}", response_model=OrgOut)
async def get_org(
    org_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(
            Organization.id == org_id, Organization.user_id == current_user.id
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )
    return org


@router.put("/{org_id}", response_model=OrgOut)
async def update_org(
    org_id: uuid.UUID,
    org_in: OrgUpdate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(
            Organization.id == org_id, Organization.user_id == current_user.id
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    update_data = org_in.model_dump(exclude_unset=True)
    guidelines_changed = "global_guidelines" in update_data
    for field, value in update_data.items():
        setattr(org, field, value)

    if guidelines_changed and not (org.global_guidelines or "").strip():
        await db.execute(
            delete(KnowledgeEmbedding).where(
                KnowledgeEmbedding.org_id == org.id,
                KnowledgeEmbedding.entity_type == "guideline",
                KnowledgeEmbedding.entity_id == org.id,
            )
        )
        await set_embedding_status(
            db,
            org_id=org.id,
            entity_type="guideline",
            entity_id=org.id,
            state=EmbeddingJobState.NOT_REQUIRED,
        )

    if guidelines_changed and org.global_guidelines and org.global_guidelines.strip():
        await queue_embedding_tasks(
            db,
            [(org.id, "guideline", org.id)],
            headers=correlation_headers(request),
        )
    else:
        await db.commit()
    await db.refresh(org)
    return org


@router.delete("/{org_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_org(
    org_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(Organization).where(
            Organization.id == org_id, Organization.user_id == current_user.id
        )
    )
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Organization not found"
        )

    await db.delete(org)
    await db.commit()
    return None
