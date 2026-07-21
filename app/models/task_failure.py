import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class TaskFailure(Base):


    __tablename__ = "task_failures"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    task_id: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    task_name: Mapped[str] = mapped_column(String(255), index=True)
    request_id: Mapped[str | None] = mapped_column(String(255), index=True)
    safe_context: Mapped[dict] = mapped_column(JSONB, default=dict)
    error_type: Mapped[str] = mapped_column(String(255))
    error_message: Mapped[str] = mapped_column(String(500))
    retries: Mapped[int] = mapped_column(Integer, default=0)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
