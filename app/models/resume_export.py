import enum
import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base

if TYPE_CHECKING:
    from app.models.resume import Resume


class ResumeExportState(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class ResumeExport(Base):


    __tablename__ = "resume_exports"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    resume_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("resumes.id", ondelete="CASCADE"),
        index=True,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        index=True,
    )
    state: Mapped[str] = mapped_column(
        String(20), default=ResumeExportState.PENDING.value, index=True
    )
    task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    source_json_data: Mapped[dict] = mapped_column(JSONB)
    source_kind: Mapped[str] = mapped_column(String(20))
    filename: Mapped[str] = mapped_column(String(255))
    storage_key: Mapped[str | None] = mapped_column(String(500), unique=True)
    content_type: Mapped[str] = mapped_column(
        String(100), default="application/pdf"
    )
    size_bytes: Mapped[int | None] = mapped_column(Integer)
    sha256: Mapped[str | None] = mapped_column(String(64))
    page_count: Mapped[int | None] = mapped_column(Integer)
    selectable_text: Mapped[bool] = mapped_column(Boolean, default=False)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    resume: Mapped["Resume"] = relationship("Resume", back_populates="exports")

    __table_args__ = (
        CheckConstraint(
            "state IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_resume_exports_state",
        ),
        CheckConstraint(
            "source_kind IN ('raw', 'optimized')",
            name="ck_resume_exports_source_kind",
        ),
        CheckConstraint("attempts >= 0", name="ck_resume_exports_attempts"),
        CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0",
            name="ck_resume_exports_positive_size",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_resume_exports_positive_pages",
        ),
    )
