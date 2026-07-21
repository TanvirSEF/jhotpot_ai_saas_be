


from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "c3a92026d8f1"
down_revision: Union[str, None] = "a8d492f16b22"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "products",
        sa.Column("category", sa.String(length=100), nullable=True),
    )
    op.add_column(
        "products",
        sa.Column(
            "attributes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


    op.execute(
        """
        DELETE FROM knowledge_embeddings AS older
        USING knowledge_embeddings AS newer
        WHERE older.org_id = newer.org_id
          AND older.entity_type = newer.entity_type
          AND older.entity_id = newer.entity_id
          AND older.entity_id IS NOT NULL
          AND (
              older.created_at < newer.created_at
              OR (older.created_at = newer.created_at AND older.id < newer.id)
          );
        """
    )
    op.create_unique_constraint(
        "uq_knowledge_embeddings_entity",
        "knowledge_embeddings",
        ["org_id", "entity_type", "entity_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_knowledge_embeddings_entity",
        "knowledge_embeddings",
        type_="unique",
    )
    op.drop_column("products", "attributes")
    op.drop_column("products", "category")
