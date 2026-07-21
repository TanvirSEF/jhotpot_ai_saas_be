


from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d74e2d9f4a10"
down_revision: Union[str, None] = "c3a92026d8f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "task_failures",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=False),
        sa.Column("task_name", sa.String(length=255), nullable=False),
        sa.Column("request_id", sa.String(length=255), nullable=True),
        sa.Column("safe_context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("error_type", sa.String(length=255), nullable=False),
        sa.Column("error_message", sa.String(length=500), nullable=False),
        sa.Column("retries", sa.Integer(), nullable=False),
        sa.Column("failed_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_task_failures_task_id", "task_failures", ["task_id"], unique=True)
    op.create_index("ix_task_failures_task_name", "task_failures", ["task_name"], unique=False)
    op.create_index("ix_task_failures_request_id", "task_failures", ["request_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_task_failures_request_id", table_name="task_failures")
    op.drop_index("ix_task_failures_task_name", table_name="task_failures")
    op.drop_index("ix_task_failures_task_id", table_name="task_failures")
    op.drop_table("task_failures")
