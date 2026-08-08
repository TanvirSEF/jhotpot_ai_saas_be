import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, EmailStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_superadmin
from app.db.session import get_db
from app.models import (
    FbPage,
    Organization,
    Resume,
    User,
)

router = APIRouter(prefix="/admin", tags=["superadmin"])


# ─── Response Schemas ────────────────────────────────────────────────────────


class AdminUserOut(BaseModel):
    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    is_active: bool
    is_superadmin: bool
    created_at: datetime
    org_count: int = 0

    model_config = ConfigDict(from_attributes=True)


class AdminUserDetailOut(AdminUserOut):
    organizations: list[str] = []  # list of business_name strings


class PlatformStatsOut(BaseModel):
    total_users: int
    active_users: int
    suspended_users: int
    total_organizations: int
    total_fb_pages: int
    active_fb_pages: int
    total_resumes: int


# ─── User Management Endpoints ───────────────────────────────────────────────


@router.get(
    "/users",
    response_model=list[AdminUserOut],
    summary="List all platform users",
)
async def list_users(
    search: str | None = Query(None, description="Filter by email or name"),
    is_active: bool | None = Query(None, description="Filter by active status"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _: User = Depends(get_current_superadmin),
    db: AsyncSession = Depends(get_db),
) -> list[AdminUserOut]:
    """Return a paginated list of all users. Superadmin only."""
    stmt = select(User)

    if search:
        pattern = f"%{search.lower()}%"
        stmt = stmt.where(
            User.email.ilike(pattern) | User.full_name.ilike(pattern)
        )
    if is_active is not None:
        stmt = stmt.where(User.is_active == is_active)

    stmt = stmt.order_by(User.created_at.desc()).offset(skip).limit(limit)
    result = await db.execute(stmt)
    users = result.scalars().all()

    output = []
    for user in users:
        # Count orgs per user
        org_count_result = await db.execute(
            select(func.count()).where(Organization.user_id == user.id)
        )
        org_count = org_count_result.scalar_one()
        output.append(
            AdminUserOut(
                id=user.id,
                email=user.email,
                full_name=user.full_name,
                is_active=user.is_active,
                is_superadmin=user.is_superadmin,
                created_at=user.created_at,
                org_count=org_count,
            )
        )
    return output


@router.get(
    "/users/{user_id}",
    response_model=AdminUserDetailOut,
    summary="Get full profile of a specific user",
)
async def get_user_detail(
    user_id: uuid.UUID,
    _: User = Depends(get_current_superadmin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserDetailOut:
    """Return detailed profile including organizations. Superadmin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    orgs_result = await db.execute(
        select(Organization).where(Organization.user_id == user.id)
    )
    orgs = orgs_result.scalars().all()

    return AdminUserDetailOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        created_at=user.created_at,
        org_count=len(orgs),
        organizations=[org.business_name for org in orgs],
    )


@router.patch(
    "/users/{user_id}/activate",
    response_model=AdminUserOut,
    summary="Re-activate a suspended user account",
)
async def activate_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_superadmin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    """Set is_active = True for a user. Superadmin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot modify your own account status.",
        )

    user.is_active = True
    await db.commit()
    await db.refresh(user)

    org_count_result = await db.execute(
        select(func.count()).where(Organization.user_id == user.id)
    )
    return AdminUserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        created_at=user.created_at,
        org_count=org_count_result.scalar_one(),
    )


@router.patch(
    "/users/{user_id}/deactivate",
    response_model=AdminUserOut,
    summary="Suspend a user account (blocks login)",
)
async def deactivate_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_superadmin),
    db: AsyncSession = Depends(get_db),
) -> AdminUserOut:
    """Set is_active = False for a user, preventing login. Superadmin only."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot suspend your own account.",
        )
    if user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot suspend another superadmin account.",
        )

    user.is_active = False
    await db.commit()
    await db.refresh(user)

    org_count_result = await db.execute(
        select(func.count()).where(Organization.user_id == user.id)
    )
    return AdminUserOut(
        id=user.id,
        email=user.email,
        full_name=user.full_name,
        is_active=user.is_active,
        is_superadmin=user.is_superadmin,
        created_at=user.created_at,
        org_count=org_count_result.scalar_one(),
    )


@router.delete(
    "/users/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Permanently delete a user and all their data",
)
async def delete_user(
    user_id: uuid.UUID,
    admin: User = Depends(get_current_superadmin),
    db: AsyncSession = Depends(get_db),
) -> None:
    """
    Hard delete a user. All related data (orgs, pages, knowledge, resumes)
    will be removed via CASCADE. Superadmin only.
    """
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if user.id == admin.id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="You cannot delete your own account.",
        )
    if user.is_superadmin:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot delete another superadmin account.",
        )

    await db.delete(user)
    await db.commit()
    return None


# ─── Platform Stats Endpoint ─────────────────────────────────────────────────


@router.get(
    "/stats",
    response_model=PlatformStatsOut,
    summary="Get platform-wide statistics",
)
async def get_platform_stats(
    _: User = Depends(get_current_superadmin),
    db: AsyncSession = Depends(get_db),
) -> PlatformStatsOut:
    """Return aggregate counts across the platform. Superadmin only."""

    total_users = await db.scalar(select(func.count(User.id)))
    active_users = await db.scalar(
        select(func.count(User.id)).where(User.is_active == True)  # noqa: E712
    )
    suspended_users = await db.scalar(
        select(func.count(User.id)).where(User.is_active == False)  # noqa: E712
    )
    total_organizations = await db.scalar(select(func.count(Organization.id)))
    total_fb_pages = await db.scalar(select(func.count(FbPage.id)))
    active_fb_pages = await db.scalar(
        select(func.count(FbPage.id)).where(FbPage.is_bot_active == True)  # noqa: E712
    )
    total_resumes = await db.scalar(select(func.count(Resume.id)))

    return PlatformStatsOut(
        total_users=total_users or 0,
        active_users=active_users or 0,
        suspended_users=suspended_users or 0,
        total_organizations=total_organizations or 0,
        total_fb_pages=total_fb_pages or 0,
        active_fb_pages=active_fb_pages or 0,
        total_resumes=total_resumes or 0,
    )
