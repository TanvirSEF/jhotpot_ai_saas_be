"""Add content-free RAG grounding and usage metrics.

Revision ID: b82e4d17a930
Revises: a71d9c42e810
Create Date: 2026-07-20 22:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b82e4d17a930"
down_revision: Union[str, None] = "a71d9c42e810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "rag_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_event_id", sa.Uuid(), nullable=False),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("prompt_version", sa.String(50), nullable=False),
        sa.Column("outcome", sa.String(20), nullable=False),
        sa.Column("fallback_reason", sa.String(100), nullable=True),
        sa.Column("retrieval_count", sa.Integer(), nullable=False),
        sa.Column("top_similarity", sa.Float(), nullable=True),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False),
        sa.Column("completion_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("reply_length", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "retrieval_count >= 0 AND prompt_tokens >= 0 AND "
            "completion_tokens >= 0 AND total_tokens >= 0 AND reply_length >= 0",
            name="ck_rag_runs_nonnegative_metrics",
        ),
        sa.CheckConstraint(
            "outcome IN ('generated', 'fallback')",
            name="ck_rag_runs_outcome",
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["webhook_event_id"], ["webhook_events.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_rag_runs_org_id", "rag_runs", ["org_id"])
    op.create_index(
        "ix_rag_runs_webhook_event_id", "rag_runs", ["webhook_event_id"]
    )
    op.create_index(
        "ix_rag_runs_prompt_version", "rag_runs", ["prompt_version"]
    )
    op.create_index("ix_rag_runs_outcome", "rag_runs", ["outcome"])
    op.create_index(
        "ix_rag_runs_fallback_reason", "rag_runs", ["fallback_reason"]
    )


def downgrade() -> None:
    op.drop_index("ix_rag_runs_fallback_reason", table_name="rag_runs")
    op.drop_index("ix_rag_runs_outcome", table_name="rag_runs")
    op.drop_index("ix_rag_runs_prompt_version", table_name="rag_runs")
    op.drop_index("ix_rag_runs_webhook_event_id", table_name="rag_runs")
    op.drop_index("ix_rag_runs_org_id", table_name="rag_runs")
    op.drop_table("rag_runs")
