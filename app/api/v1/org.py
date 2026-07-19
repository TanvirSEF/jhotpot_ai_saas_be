import uuid
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import Organization, User

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

    class Config:
        from_attributes = True


@router.post("", response_model=OrgOut, status_code=status.HTTP_201_CREATED)
async def create_org(
    org_in: OrgCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    org = Organization(
        user_id=current_user.id,
        business_name=org_in.business_name,
        global_guidelines=org_in.global_guidelines,
    )
    db.add(org)
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

    if org_in.business_name is not None:
        org.business_name = org_in.business_name
    if org_in.global_guidelines is not None:
        org.global_guidelines = org_in.global_guidelines

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
