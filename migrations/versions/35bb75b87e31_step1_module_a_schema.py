"""step1_module_a_schema

Revision ID: 35bb75b87e31
Revises: e79e777a4bcc
Create Date: 2026-07-20 00:06:57.669638

MANUAL EDITS:
  1. users.id INTEGER → UUID: PostgreSQL cannot ALTER a PRIMARY KEY column type
     in-place. We DROP + recreate the users table (dev env, no real data).
  2. Added HNSW index on knowledge_embeddings.embedding for sub-20ms
     cosine similarity search (PRD §6.1).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

import pgvector.sqlalchemy  # noqa: F401 — registers Vector type with SQLAlchemy

# revision identifiers, used by Alembic.
revision: str = '35bb75b87e31'
down_revision: Union[str, None] = 'e79e777a4bcc'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── 1. Recreate `users` with UUID primary key ───────────────────────────
    # PostgreSQL cannot directly cast INTEGER → UUID on a PK column.
    # Since this is a development environment with no production data, we
    # drop and recreate the table cleanly.
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')

    op.create_table(
        'users',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=True),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    # ── 2. Create Module A tables ───────────────────────────────────────────
    op.create_table(
        'organizations',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('user_id', sa.Uuid(), nullable=False),
        sa.Column('business_name', sa.String(length=255), nullable=False),
        sa.Column('global_guidelines', sa.Text(), nullable=True),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_organizations_user_id'), 'organizations', ['user_id'], unique=False)

    op.create_table(
        'faqs',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('question', sa.Text(), nullable=False),
        sa.Column('answer', sa.Text(), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_faqs_org_id'), 'faqs', ['org_id'], unique=False)

    op.create_table(
        'fb_pages',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('page_id', sa.String(length=255), nullable=False),
        sa.Column('page_name', sa.String(length=255), nullable=True),
        sa.Column('encrypted_access_token', sa.Text(), nullable=False),
        sa.Column('is_bot_active', sa.Boolean(), nullable=False, server_default=sa.text('true')),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('page_id'),
    )
    op.create_index(op.f('ix_fb_pages_org_id'), 'fb_pages', ['org_id'], unique=False)

    op.create_table(
        'knowledge_embeddings',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('embedding', pgvector.sqlalchemy.Vector(1536), nullable=True),
        sa.Column('entity_type', sa.String(length=50), nullable=False),
        sa.Column('entity_id', sa.Uuid(), nullable=True),
        sa.Column('metadata', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_knowledge_embeddings_entity_type'),
        'knowledge_embeddings', ['entity_type'], unique=False,
    )
    op.create_index(
        'ix_knowledge_embeddings_org_entity',
        'knowledge_embeddings', ['org_id', 'entity_type'], unique=False,
    )
    op.create_index(
        op.f('ix_knowledge_embeddings_org_id'),
        'knowledge_embeddings', ['org_id'], unique=False,
    )

    # ── 3. HNSW index for pgvector cosine similarity search ─────────────────
    # PRD §6.1: vector search must complete in < 20ms at scale.
    # HNSW (Hierarchical Navigable Small World) provides approximate nearest
    # neighbour search in O(log n) vs sequential scan O(n).
    # m=16, ef_construction=64 are production-standard starting values.
    op.execute(
        """
        CREATE INDEX ix_knowledge_embeddings_embedding_hnsw
        ON knowledge_embeddings
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64);
        """
    )

    op.create_table(
        'products',
        sa.Column('id', sa.Uuid(), nullable=False),
        sa.Column('org_id', sa.Uuid(), nullable=False),
        sa.Column('name', sa.String(length=255), nullable=False),
        sa.Column('sku', sa.String(length=100), nullable=True),
        sa.Column('price', sa.DECIMAL(precision=10, scale=2), nullable=False),
        sa.Column(
            'stock_status',
            sa.Enum('IN_STOCK', 'OUT_OF_STOCK', name='stock_status_enum'),
            nullable=False,
            server_default='IN_STOCK',
        ),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(['org_id'], ['organizations.id'], ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_products_org_id'), 'products', ['org_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_products_org_id'), table_name='products')
    op.drop_table('products')

    op.execute('DROP INDEX IF EXISTS ix_knowledge_embeddings_embedding_hnsw;')

    op.drop_index(op.f('ix_knowledge_embeddings_org_id'), table_name='knowledge_embeddings')
    op.drop_index('ix_knowledge_embeddings_org_entity', table_name='knowledge_embeddings')
    op.drop_index(op.f('ix_knowledge_embeddings_entity_type'), table_name='knowledge_embeddings')
    op.drop_table('knowledge_embeddings')

    op.drop_index(op.f('ix_fb_pages_org_id'), table_name='fb_pages')
    op.drop_table('fb_pages')

    op.drop_index(op.f('ix_faqs_org_id'), table_name='faqs')
    op.drop_table('faqs')

    op.drop_index(op.f('ix_organizations_user_id'), table_name='organizations')
    op.drop_table('organizations')

    # Restore users table with INTEGER PK (initial state)
    op.drop_index('ix_users_email', table_name='users')
    op.drop_table('users')

    op.create_table(
        'users',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('email', sa.String(length=255), nullable=False),
        sa.Column('full_name', sa.String(length=255), nullable=True),
        sa.Column('hashed_password', sa.String(length=255), nullable=False),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_users_email'), 'users', ['email'], unique=True)

    op.execute('DROP TYPE IF EXISTS stock_status_enum;')
