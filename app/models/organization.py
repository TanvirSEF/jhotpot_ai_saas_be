import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.session import Base


class Organization(Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    business_name: Mapped[str] = mapped_column(String(255))
    global_guidelines: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    user: Mapped["User"] = relationship("User", back_populates="organizations")
    fb_pages: Mapped[list["FbPage"]] = relationship(
        "FbPage", back_populates="organization", cascade="all, delete-orphan"
    )
    products: Mapped[list["Product"]] = relationship(
        "Product", back_populates="organization", cascade="all, delete-orphan"
    )
    faqs: Mapped[list["Faq"]] = relationship(
        "Faq", back_populates="organization", cascade="all, delete-orphan"
    )
    knowledge_embeddings: Mapped[list["KnowledgeEmbedding"]] = relationship(
        "KnowledgeEmbedding", back_populates="organization", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Organization {self.business_name}>"
