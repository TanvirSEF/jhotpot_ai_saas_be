import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class FbPage(Base):
    __tablename__ = "fb_pages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("organizations.id", ondelete="CASCADE"), index=True
    )
    page_id: Mapped[str] = mapped_column(String(255), unique=True)
    page_name: Mapped[str | None] = mapped_column(String(255))
    encrypted_access_token: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_bot_active: Mapped[bool] = mapped_column(Boolean, default=False)
    connection_status: Mapped[str] = mapped_column(String(30), default="connected", index=True)
    subscription_status: Mapped[str] = mapped_column(String(30), default="pending", index=True)
    token_status: Mapped[str] = mapped_column(String(30), default="unknown", index=True)
    subscribed_fields: Mapped[list[str]] = mapped_column(JSONB, default=list)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    data_access_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_token_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_subscription_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "connection_status IN ('connected', 'needs_reauth', 'disconnected')",
            name="ck_fb_pages_connection_status",
        ),
        CheckConstraint(
            "subscription_status IN ('pending', 'subscribed', 'failed', 'unsubscribed')",
            name="ck_fb_pages_subscription_status",
        ),
        CheckConstraint(
            "token_status IN ('unknown', 'valid', 'invalid', 'expired', 'insufficient_scope', 'missing')",
            name="ck_fb_pages_token_status",
        ),
    )

    organization: Mapped["Organization"] = relationship("Organization", back_populates="fb_pages")

    def __repr__(self) -> str:
        return f"<FbPage {self.page_id}>"
