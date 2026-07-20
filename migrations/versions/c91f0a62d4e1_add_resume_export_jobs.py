"""Add durable resume PDF export jobs.

Revision ID: c91f0a62d4e1
Revises: b82e4d17a930
Create Date: 2026-07-20 23:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c91f0a62d4e1"
down_revision: Union[str, None] = "b82e4d17a930"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "resume_exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("resume_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("task_id", sa.String(255), nullable=True),
        sa.Column("source_json_data", postgresql.JSONB(), nullable=False),
        sa.Column("source_kind", sa.String(20), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(500), nullable=True),
        sa.Column("content_type", sa.String(100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=True),
        sa.Column("sha256", sa.String(64), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("selectable_text", sa.Boolean(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('pending', 'processing', 'ready', 'failed')",
            name="ck_resume_exports_state",
        ),
        sa.CheckConstraint(
            "source_kind IN ('raw', 'optimized')",
            name="ck_resume_exports_source_kind",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_resume_exports_attempts"),
        sa.CheckConstraint(
            "size_bytes IS NULL OR size_bytes > 0",
            name="ck_resume_exports_positive_size",
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count > 0",
            name="ck_resume_exports_positive_pages",
        ),
        sa.ForeignKeyConstraint(["resume_id"], ["resumes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key"),
    )
    op.create_index("ix_resume_exports_resume_id", "resume_exports", ["resume_id"])
    op.create_index("ix_resume_exports_user_id", "resume_exports", ["user_id"])
    op.create_index("ix_resume_exports_state", "resume_exports", ["state"])
    op.create_index("ix_resume_exports_task_id", "resume_exports", ["task_id"])


def downgrade() -> None:
    op.drop_index("ix_resume_exports_task_id", table_name="resume_exports")
    op.drop_index("ix_resume_exports_state", table_name="resume_exports")
    op.drop_index("ix_resume_exports_user_id", table_name="resume_exports")
    op.drop_index("ix_resume_exports_resume_id", table_name="resume_exports")
    op.drop_table("resume_exports")
