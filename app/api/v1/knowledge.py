"""
Knowledge Base API — Phase A1 (Module A)

Endpoints:
  Products
  --------
  POST   /api/v1/knowledge/{org_id}/products          – Create product + queue embedding
  GET    /api/v1/knowledge/{org_id}/products          – List products (paginated)
  GET    /api/v1/knowledge/{org_id}/products/{id}     – Get single product
  PUT    /api/v1/knowledge/{org_id}/products/{id}     – Full update + re-embed
  DELETE /api/v1/knowledge/{org_id}/products/{id}     – Delete product + its embeddings

  FAQs
  ----
  POST   /api/v1/knowledge/{org_id}/faqs              – Create FAQ + queue embedding
  GET    /api/v1/knowledge/{org_id}/faqs              – List FAQs
  GET    /api/v1/knowledge/{org_id}/faqs/{id}         – Get single FAQ
  PUT    /api/v1/knowledge/{org_id}/faqs/{id}         – Update FAQ + re-embed
  DELETE /api/v1/knowledge/{org_id}/faqs/{id}         – Delete FAQ + its embeddings

  Diagnostic
  ----------
  GET    /api/v1/knowledge/{org_id}/search            – Semantic search test

Design notes:
  * All mutating endpoints verify org ownership via `_get_owned_org()`.
  * Embedding generation is always queued as a Celery background task so the
    HTTP response is never delayed by OpenAI round-trips.
  * Existing embeddings for an entity are deleted before re-embedding to
    prevent stale duplicate vectors.
  * Pagination on list endpoints uses limit/offset for simplicity; can be
    migrated to cursor-based later.
"""

import uuid
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.db.session import get_db
from app.models import Faq, KnowledgeEmbedding, Organization, Product, StockStatus, User
from app.worker.tasks import generate_embeddings

router = APIRouter(prefix="/knowledge", tags=["knowledge"])

# ──────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ──────────────────────────────────────────────────────────────────────────────

async def _get_owned_org(
    org_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Organization:
    """Fetch org and assert current_user is the owner. Raises 404 / 403."""
    result = await db.execute(select(Organization).where(Organization.id == org_id))
    org = result.scalar_one_or_none()
    if not org:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Organization not found.")
    if org.user_id != current_user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Access denied.")
    return org


async def _delete_entity_embeddings(
    db: AsyncSession, org_id: uuid.UUID, entity_type: str, entity_id: uuid.UUID
) -> None:
    """Remove all knowledge vectors for a specific entity (pre-update / delete)."""
    await db.execute(
        delete(KnowledgeEmbedding).where(
            KnowledgeEmbedding.org_id == org_id,
            KnowledgeEmbedding.entity_type == entity_type,
            KnowledgeEmbedding.entity_id == entity_id,
        )
    )


# ──────────────────────────────────────────────────────────────────────────────
# Product Schemas
# ──────────────────────────────────────────────────────────────────────────────

class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    sku: str | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=100)
    attributes: dict[str, str | list[str]] | None = None
    price: Decimal = Field(..., gt=0, decimal_places=2)
    stock_status: StockStatus = StockStatus.IN_STOCK
    description: str | None = None


class ProductUpdate(BaseModel):
    name: str | None = Field(None, min_length=1, max_length=255)
    sku: str | None = Field(None, max_length=100)
    category: str | None = Field(None, max_length=100)
    attributes: dict[str, str | list[str]] | None = None
    price: Decimal | None = Field(None, gt=0, decimal_places=2)
    stock_status: StockStatus | None = None
    description: str | None = None


class ProductOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    name: str
    sku: str | None
    category: str | None
    attributes: dict | None
    price: Decimal
    stock_status: StockStatus
    description: str | None

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────────────────────────────────────────
# Product Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{org_id}/products",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    org_id: uuid.UUID,
    body: ProductCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductOut:
    await _get_owned_org(org_id, current_user, db)

    product = Product(
        org_id=org_id,
        name=body.name,
        sku=body.sku,
        category=body.category,
        attributes=body.attributes,
        price=body.price,
        stock_status=body.stock_status,
        description=body.description,
    )
    db.add(product)
    await db.commit()
    await db.refresh(product)

    # Queue background embedding generation
    generate_embeddings.delay("product", str(product.id))

    return product


@router.get("/{org_id}/products", response_model=list[ProductOut])
async def list_products(
    org_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ProductOut]:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Product)
        .where(Product.org_id == org_id)
        .order_by(Product.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return result.scalars().all()


@router.get("/{org_id}/products/{product_id}", response_model=ProductOut)
async def get_product(
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductOut:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.org_id == org_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")
    return product


@router.put("/{org_id}/products/{product_id}", response_model=ProductOut)
async def update_product(
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    body: ProductUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductOut:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.org_id == org_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    # Apply partial updates
    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)

    await db.commit()
    await db.refresh(product)

    # Purge stale vectors then re-generate
    await _delete_entity_embeddings(db, org_id, "product", product_id)
    await db.commit()
    generate_embeddings.delay("product", str(product.id))

    return product


@router.delete("/{org_id}/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_product(
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.org_id == org_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")

    # Delete embeddings first (FK cascade handles it, but explicit is safer)
    await _delete_entity_embeddings(db, org_id, "product", product_id)
    await db.delete(product)
    await db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# FAQ Schemas
# ──────────────────────────────────────────────────────────────────────────────

class FaqCreate(BaseModel):
    question: str = Field(..., min_length=1)
    answer: str = Field(..., min_length=1)


class FaqUpdate(BaseModel):
    question: str | None = Field(None, min_length=1)
    answer: str | None = Field(None, min_length=1)


class FaqOut(BaseModel):
    id: uuid.UUID
    org_id: uuid.UUID
    question: str
    answer: str

    model_config = {"from_attributes": True}


# ──────────────────────────────────────────────────────────────────────────────
# FAQ Endpoints
# ──────────────────────────────────────────────────────────────────────────────

@router.post(
    "/{org_id}/faqs",
    response_model=FaqOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_faq(
    org_id: uuid.UUID,
    body: FaqCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FaqOut:
    await _get_owned_org(org_id, current_user, db)

    faq = Faq(org_id=org_id, question=body.question, answer=body.answer)
    db.add(faq)
    await db.commit()
    await db.refresh(faq)

    generate_embeddings.delay("faq", str(faq.id))
    return faq


@router.get("/{org_id}/faqs", response_model=list[FaqOut])
async def list_faqs(
    org_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[FaqOut]:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Faq).where(Faq.org_id == org_id).order_by(Faq.created_at.desc())
    )
    return result.scalars().all()


@router.get("/{org_id}/faqs/{faq_id}", response_model=FaqOut)
async def get_faq(
    org_id: uuid.UUID,
    faq_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FaqOut:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Faq).where(Faq.id == faq_id, Faq.org_id == org_id)
    )
    faq = result.scalar_one_or_none()
    if not faq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ not found.")
    return faq


@router.put("/{org_id}/faqs/{faq_id}", response_model=FaqOut)
async def update_faq(
    org_id: uuid.UUID,
    faq_id: uuid.UUID,
    body: FaqUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FaqOut:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Faq).where(Faq.id == faq_id, Faq.org_id == org_id)
    )
    faq = result.scalar_one_or_none()
    if not faq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ not found.")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(faq, field, value)

    await db.commit()
    await db.refresh(faq)

    await _delete_entity_embeddings(db, org_id, "faq", faq_id)
    await db.commit()
    generate_embeddings.delay("faq", str(faq.id))

    return faq


@router.delete("/{org_id}/faqs/{faq_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_faq(
    org_id: uuid.UUID,
    faq_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Faq).where(Faq.id == faq_id, Faq.org_id == org_id)
    )
    faq = result.scalar_one_or_none()
    if not faq:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "FAQ not found.")

    await _delete_entity_embeddings(db, org_id, "faq", faq_id)
    await db.delete(faq)
    await db.commit()


# ──────────────────────────────────────────────────────────────────────────────
# Diagnostic: Vector Similarity Search
# ──────────────────────────────────────────────────────────────────────────────

class SearchResult(BaseModel):
    entity_type: str
    entity_id: uuid.UUID | None
    content: str
    similarity: float


@router.get("/{org_id}/search", response_model=list[SearchResult])
async def semantic_search(
    org_id: uuid.UUID,
    q: Annotated[str, Query(min_length=1, description="Natural-language query to embed and match.")],
    top_k: Annotated[int, Query(ge=1, le=10)] = 4,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[SearchResult]:
    """
    Diagnostic endpoint: embed *q* on-the-fly and return the top-k most
    semantically similar knowledge chunks for the given organisation.

    This mirrors the retrieval step inside the Celery RAG worker so
    merchants can validate their knowledge base quality without needing to
    trigger a real Facebook event.
    """
    from app.services.embedding import get_embedding

    await _get_owned_org(org_id, current_user, db)

    query_vector = await get_embedding(q)

    # pgvector cosine distance: <=> operator (lower = closer).
    # We convert to similarity = 1 - distance for human-readable output.
    rows = await db.execute(
        select(
            KnowledgeEmbedding.entity_type,
            KnowledgeEmbedding.entity_id,
            KnowledgeEmbedding.content,
            (
                1 - KnowledgeEmbedding.embedding.cosine_distance(query_vector)
            ).label("similarity"),
        )
        .where(
            KnowledgeEmbedding.org_id == org_id,
            KnowledgeEmbedding.embedding.is_not(None),
        )
        .order_by(KnowledgeEmbedding.embedding.cosine_distance(query_vector))
        .limit(top_k)
    )

    return [
        SearchResult(
            entity_type=row.entity_type,
            entity_id=row.entity_id,
            content=row.content,
            similarity=round(float(row.similarity), 4),
        )
        for row in rows
    ]
