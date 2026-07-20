"""Add the durable, idempotent Meta webhook inbox.

Revision ID: a71d9c42e810
Revises: f96c18a7d253
Create Date: 2026-07-20 20:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a71d9c42e810"
down_revision: Union[str, None] = "f96c18a7d253"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("fb_page_id", sa.Uuid(), nullable=False),
        sa.Column("provider_event_id", sa.String(255), nullable=False),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("state", sa.String(30), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("event_timestamp", sa.BigInteger(), nullable=False),
        sa.Column("request_id", sa.String(255), nullable=True),
        sa.Column("celery_task_id", sa.String(255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(100), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "processing_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column(
            "delivery_started_at", sa.DateTime(timezone=True), nullable=True
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "attempts >= 0", name="ck_webhook_events_attempts"
        ),
        sa.CheckConstraint(
            "event_type IN ('MessengerEvent', 'CommentEvent')",
            name="ck_webhook_events_type",
        ),
        sa.CheckConstraint(
            "state IN ('accepted', 'queued', 'processing', 'delivering', "
            "'retrying', 'succeeded', 'skipped', 'failed')",
            name="ck_webhook_events_state",
        ),
        sa.ForeignKeyConstraint(
            ["fb_page_id"], ["fb_pages.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["org_id"], ["organizations.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "fb_page_id",
            "event_type",
            "provider_event_id",
            name="uq_webhook_events_provider_event",
        ),
    )
    op.create_index("ix_webhook_events_org_id", "webhook_events", ["org_id"])
    op.create_index(
        "ix_webhook_events_fb_page_id", "webhook_events", ["fb_page_id"]
    )
    op.create_index(
        "ix_webhook_events_event_type", "webhook_events", ["event_type"]
    )
    op.create_index("ix_webhook_events_state", "webhook_events", ["state"])
    op.create_index(
        "ix_webhook_events_request_id", "webhook_events", ["request_id"]
    )
    op.create_index(
        "ix_webhook_events_celery_task_id",
        "webhook_events",
        ["celery_task_id"],
    )
    op.create_index(
        "ix_webhook_events_recovery",
        "webhook_events",
        ["state", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_webhook_events_recovery", table_name="webhook_events")
    op.drop_index("ix_webhook_events_celery_task_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_request_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_state", table_name="webhook_events")
    op.drop_index("ix_webhook_events_event_type", table_name="webhook_events")
    op.drop_index("ix_webhook_events_fb_page_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_org_id", table_name="webhook_events")
    op.drop_table("webhook_events")
