


from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e85b7f31c642"
down_revision: Union[str, None] = "d74e2d9f4a10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "embedding_statuses",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("org_id", sa.Uuid(), nullable=False),
        sa.Column("entity_type", sa.String(length=50), nullable=False),
        sa.Column("entity_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("task_id", sa.String(length=255), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("last_error_code", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("attempts >= 0", name="ck_embedding_statuses_attempts"),
        sa.CheckConstraint("entity_type IN ('product', 'faq', 'guideline')", name="ck_embedding_statuses_entity_type"),
        sa.CheckConstraint("state IN ('pending', 'processing', 'ready', 'failed', 'not_required', 'missing')", name="ck_embedding_statuses_state"),
        sa.ForeignKeyConstraint(["org_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("org_id", "entity_type", "entity_id", name="uq_embedding_statuses_entity"),
    )
    op.create_index("ix_embedding_statuses_org_id", "embedding_statuses", ["org_id"], unique=False)
    op.create_index("ix_embedding_statuses_entity_type", "embedding_statuses", ["entity_type"], unique=False)
    op.create_index("ix_embedding_statuses_state", "embedding_statuses", ["state"], unique=False)
    op.create_index("ix_embedding_statuses_task_id", "embedding_statuses", ["task_id"], unique=False)


    op.execute(
        """
        INSERT INTO embedding_statuses (
            id, org_id, entity_type, entity_id, state, attempts, content_hash
        )
        SELECT gen_random_uuid(), product.org_id, 'product', product.id,
               CASE WHEN embedding.id IS NULL THEN 'missing' ELSE 'ready' END,
               0, NULL
        FROM products AS product
        LEFT JOIN knowledge_embeddings AS embedding
         ON embedding.org_id = product.org_id
         AND embedding.entity_type = 'product'
         AND embedding.entity_id = product.id;
        """
    )

    op.execute(
        """
        INSERT INTO embedding_statuses (
            id, org_id, entity_type, entity_id, state, attempts, content_hash
        )
        SELECT gen_random_uuid(), faq.org_id, 'faq', faq.id,
               CASE WHEN embedding.id IS NULL THEN 'missing' ELSE 'ready' END,
               0, NULL
        FROM faqs AS faq
        LEFT JOIN knowledge_embeddings AS embedding
         ON embedding.org_id = faq.org_id
         AND embedding.entity_type = 'faq'
         AND embedding.entity_id = faq.id;
        """
    )

    op.execute(
        """
        INSERT INTO embedding_statuses (
            id, org_id, entity_type, entity_id, state, attempts, content_hash
        )
        SELECT gen_random_uuid(), organization.id, 'guideline', organization.id,
               CASE
                   WHEN btrim(coalesce(organization.global_guidelines, '')) = ''
                       THEN 'not_required'
                   WHEN embedding.id IS NULL THEN 'missing'
                   ELSE 'ready'
               END,
               0, NULL
        FROM organizations AS organization
        LEFT JOIN knowledge_embeddings AS embedding
          ON embedding.org_id = organization.id
         AND embedding.entity_type = 'guideline'
         AND embedding.entity_id = organization.id;
        """
    )


def downgrade() -> None:
    op.drop_index("ix_embedding_statuses_task_id", table_name="embedding_statuses")
    op.drop_index("ix_embedding_statuses_state", table_name="embedding_statuses")
    op.drop_index("ix_embedding_statuses_entity_type", table_name="embedding_statuses")
    op.drop_index("ix_embedding_statuses_org_id", table_name="embedding_statuses")
    op.drop_table("embedding_statuses")
