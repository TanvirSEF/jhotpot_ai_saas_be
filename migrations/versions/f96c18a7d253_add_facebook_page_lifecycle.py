


from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f96c18a7d253"
down_revision: Union[str, None] = "e85b7f31c642"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column("fb_pages", "encrypted_access_token", existing_type=sa.Text(), nullable=True)
    op.alter_column(
        "fb_pages",
        "is_bot_active",
        existing_type=sa.Boolean(),
        server_default=sa.false(),
    )
    op.add_column("fb_pages", sa.Column("connection_status", sa.String(30), server_default="connected", nullable=False))
    op.add_column("fb_pages", sa.Column("subscription_status", sa.String(30), server_default="pending", nullable=False))
    op.add_column("fb_pages", sa.Column("token_status", sa.String(30), server_default="unknown", nullable=False))
    op.add_column("fb_pages", sa.Column("subscribed_fields", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False))
    op.add_column("fb_pages", sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fb_pages", sa.Column("data_access_expires_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fb_pages", sa.Column("last_token_check_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fb_pages", sa.Column("last_subscription_attempt_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("fb_pages", sa.Column("last_error_code", sa.String(100), nullable=True))
    op.add_column("fb_pages", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.add_column("fb_pages", sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE fb_pages SET is_bot_active = false")
    op.create_check_constraint("ck_fb_pages_connection_status", "fb_pages", "connection_status IN ('connected', 'needs_reauth', 'disconnected')")
    op.create_check_constraint("ck_fb_pages_subscription_status", "fb_pages", "subscription_status IN ('pending', 'subscribed', 'failed', 'unsubscribed')")
    op.create_check_constraint("ck_fb_pages_token_status", "fb_pages", "token_status IN ('unknown', 'valid', 'invalid', 'expired', 'insufficient_scope', 'missing')")
    op.create_index("ix_fb_pages_connection_status", "fb_pages", ["connection_status"], unique=False)
    op.create_index("ix_fb_pages_subscription_status", "fb_pages", ["subscription_status"], unique=False)
    op.create_index("ix_fb_pages_token_status", "fb_pages", ["token_status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_fb_pages_token_status", table_name="fb_pages")
    op.drop_index("ix_fb_pages_subscription_status", table_name="fb_pages")
    op.drop_index("ix_fb_pages_connection_status", table_name="fb_pages")
    op.drop_constraint("ck_fb_pages_token_status", "fb_pages", type_="check")
    op.drop_constraint("ck_fb_pages_subscription_status", "fb_pages", type_="check")
    op.drop_constraint("ck_fb_pages_connection_status", "fb_pages", type_="check")
    op.drop_column("fb_pages", "disconnected_at")
    op.drop_column("fb_pages", "updated_at")
    op.drop_column("fb_pages", "last_error_code")
    op.drop_column("fb_pages", "last_subscription_attempt_at")
    op.drop_column("fb_pages", "last_token_check_at")
    op.drop_column("fb_pages", "data_access_expires_at")
    op.drop_column("fb_pages", "token_expires_at")
    op.drop_column("fb_pages", "subscribed_fields")
    op.drop_column("fb_pages", "token_status")
    op.drop_column("fb_pages", "subscription_status")
    op.drop_column("fb_pages", "connection_status")
    op.execute("DELETE FROM fb_pages WHERE encrypted_access_token IS NULL")
    op.alter_column(
        "fb_pages",
        "is_bot_active",
        existing_type=sa.Boolean(),
        server_default=sa.true(),
    )
    op.alter_column("fb_pages", "encrypted_access_token", existing_type=sa.Text(), nullable=False)
