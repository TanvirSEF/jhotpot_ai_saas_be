import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WebhookEvent(Base):


    __tablename__ = "webhook_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        index=True,
    )
    fb_page_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("fb_pages.id", ondelete="CASCADE"),
        index=True,
    )
    provider_event_id: Mapped[str] = mapped_column(String(255))
    event_type: Mapped[str] = mapped_column(String(30), index=True)
    state: Mapped[str] = mapped_column(String(30), default="accepted", index=True)
    payload: Mapped[dict] = mapped_column(JSONB)
    event_timestamp: Mapped[int] = mapped_column(BigInteger, default=0)
    request_id: Mapped[str | None] = mapped_column(String(255), index=True)
    celery_task_id: Mapped[str | None] = mapped_column(String(255), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    queued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    processing_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    delivery_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint(
            "fb_page_id",
            "event_type",
            "provider_event_id",
            name="uq_webhook_events_provider_event",
        ),
        CheckConstraint(
            "event_type IN ('MessengerEvent', 'CommentEvent')",
            name="ck_webhook_events_type",
        ),
        CheckConstraint(
            "state IN ('accepted', 'queued', 'processing', 'delivering', "
            "'retrying', 'succeeded', 'skipped', 'failed')",
            name="ck_webhook_events_state",
        ),
        CheckConstraint("attempts >= 0", name="ck_webhook_events_attempts"),
        Index("ix_webhook_events_recovery", "state", "updated_at"),
    )
