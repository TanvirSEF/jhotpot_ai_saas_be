import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class EmbeddingJobState(str, enum.Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"
    NOT_REQUIRED = "not_required"
    MISSING = "missing"


class EmbeddingEntityType(str, enum.Enum):
    PRODUCT = "product"
    FAQ = "faq"
    GUIDELINE = "guideline"


class EmbeddingStatusRecord(Base):
    """Current generation state for one knowledge-base source entity."""

    __tablename__ = "embedding_statuses"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    entity_type: Mapped[str] = mapped_column(String(50), index=True)
    entity_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True))
    state: Mapped[str] = mapped_column(String(20), index=True)
    task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "org_id",
            "entity_type",
            "entity_id",
            name="uq_embedding_statuses_entity",
        ),
        CheckConstraint(
            "entity_type IN ('product', 'faq', 'guideline')",
            name="ck_embedding_statuses_entity_type",
        ),
        CheckConstraint(
            "state IN ('pending', 'processing', 'ready', 'failed', 'not_required', 'missing')",
            name="ck_embedding_statuses_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_embedding_statuses_attempts"),
    )
