


import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_current_user
from app.core.observability import observe_operation
from app.db.session import get_db
from app.models import (
    EmbeddingEntityType,
    EmbeddingJobState,
    EmbeddingStatusRecord,
    Faq,
    KnowledgeEmbedding,
    Organization,
    Product,
    StockStatus,
    User,
)
from app.services.embedding_status import delete_embedding_status
from app.worker.dispatch import queue_embedding_tasks
from app.worker.reliability import correlation_headers

router = APIRouter(prefix="/knowledge", tags=["knowledge"])
_MAX_REBUILD_TARGETS = 1000


async def _get_owned_org(
    org_id: uuid.UUID,
    current_user: User,
    db: AsyncSession,
) -> Organization:

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

    await db.execute(
        delete(KnowledgeEmbedding).where(
            KnowledgeEmbedding.org_id == org_id,
            KnowledgeEmbedding.entity_type == entity_type,
            KnowledgeEmbedding.entity_id == entity_id,
        )
    )
    await delete_embedding_status(
        db,
        org_id=org_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )


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


@router.post(
    "/{org_id}/products",
    response_model=ProductOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_product(
    org_id: uuid.UUID,
    body: ProductCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Product:
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
    await db.flush()


    await queue_embedding_tasks(
        db,
        [(org_id, "product", product.id)],
        headers=correlation_headers(request),
    )
    await db.refresh(product)

    return product


@router.get("/{org_id}/products", response_model=list[ProductOut])
async def list_products(
    org_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[Product]:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Product)
        .where(Product.org_id == org_id)
        .order_by(Product.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())


@router.get("/{org_id}/products/{product_id}", response_model=ProductOut)
async def get_product(
    org_id: uuid.UUID,
    product_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Product:
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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Product:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Product).where(Product.id == product_id, Product.org_id == org_id)
    )
    product = result.scalar_one_or_none()
    if not product:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Product not found.")


    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(product, field, value)


    await queue_embedding_tasks(
        db,
        [(org_id, "product", product.id)],
        headers=correlation_headers(request),
    )
    await db.refresh(product)

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


    await _delete_entity_embeddings(db, org_id, "product", product_id)
    await db.delete(product)
    await db.commit()


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


@router.post(
    "/{org_id}/faqs",
    response_model=FaqOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_faq(
    org_id: uuid.UUID,
    body: FaqCreate,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Faq:
    await _get_owned_org(org_id, current_user, db)

    faq = Faq(org_id=org_id, question=body.question, answer=body.answer)
    db.add(faq)
    await db.flush()

    await queue_embedding_tasks(
        db,
        [(org_id, "faq", faq.id)],
        headers=correlation_headers(request),
    )
    await db.refresh(faq)
    return faq


@router.get("/{org_id}/faqs", response_model=list[FaqOut])
async def list_faqs(
    org_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> list[Faq]:
    await _get_owned_org(org_id, current_user, db)

    result = await db.execute(
        select(Faq).where(Faq.org_id == org_id).order_by(Faq.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{org_id}/faqs/{faq_id}", response_model=FaqOut)
async def get_faq(
    org_id: uuid.UUID,
    faq_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Faq:
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
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Faq:
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


    await queue_embedding_tasks(
        db,
        [(org_id, "faq", faq.id)],
        headers=correlation_headers(request),
    )
    await db.refresh(faq)

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


class EmbeddingStatusOut(BaseModel):
    entity_type: str
    entity_id: uuid.UUID
    state: str
    task_id: str | None
    attempts: int
    content_hash: str | None
    last_error_code: str | None
    updated_at: datetime

    model_config = {"from_attributes": True}


class EmbeddingQueueOut(BaseModel):
    queued: int
    task_id: str | None = None


async def _assert_embedding_source_exists(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_type: EmbeddingEntityType,
    entity_id: uuid.UUID,
) -> None:
    if entity_type is EmbeddingEntityType.PRODUCT:
        statement = select(Product.id).where(Product.id == entity_id, Product.org_id == org_id)
    elif entity_type is EmbeddingEntityType.FAQ:
        statement = select(Faq.id).where(Faq.id == entity_id, Faq.org_id == org_id)
    else:
        if entity_id != org_id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "Guideline source not found.")
        statement = select(Organization.id).where(Organization.id == org_id)

    if (await db.execute(statement)).scalar_one_or_none() is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Embedding source not found.")


@router.get("/{org_id}/embedding-status", response_model=list[EmbeddingStatusOut])
async def list_embedding_statuses(
    org_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    entity_type: EmbeddingEntityType | None = None,
    state: EmbeddingJobState | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[EmbeddingStatusRecord]:
    await _get_owned_org(org_id, current_user, db)
    statement = select(EmbeddingStatusRecord).where(EmbeddingStatusRecord.org_id == org_id)
    if entity_type is not None:
        statement = statement.where(EmbeddingStatusRecord.entity_type == entity_type.value)
    if state is not None:
        statement = statement.where(EmbeddingStatusRecord.state == state.value)
    result = await db.execute(
        statement.order_by(EmbeddingStatusRecord.updated_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


@router.post(
    "/{org_id}/embeddings/{entity_type}/{entity_id}/retry",
    response_model=EmbeddingQueueOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def retry_embedding(
    org_id: uuid.UUID,
    entity_type: EmbeddingEntityType,
    entity_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmbeddingQueueOut:
    await _get_owned_org(org_id, current_user, db)
    await _assert_embedding_source_exists(
        db,
        org_id=org_id,
        entity_type=entity_type,
        entity_id=entity_id,
    )
    task_ids = await queue_embedding_tasks(
        db,
        [(org_id, entity_type.value, entity_id)],
        headers=correlation_headers(request),
    )
    return EmbeddingQueueOut(queued=1, task_id=task_ids[0])


@router.post(
    "/{org_id}/embeddings/rebuild",
    response_model=EmbeddingQueueOut,
    status_code=status.HTTP_202_ACCEPTED,
)
async def rebuild_embeddings(
    org_id: uuid.UUID,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EmbeddingQueueOut:
    await _get_owned_org(org_id, current_user, db)
    product_ids = list(
        (
            await db.execute(
                select(Product.id).where(Product.org_id == org_id).limit(_MAX_REBUILD_TARGETS + 1)
            )
        ).scalars()
    )
    faq_ids = list(
        (
            await db.execute(
                select(Faq.id).where(Faq.org_id == org_id).limit(_MAX_REBUILD_TARGETS + 1)
            )
        ).scalars()
    )
    targets = [
        *((org_id, "product", entity_id) for entity_id in product_ids),
        *((org_id, "faq", entity_id) for entity_id in faq_ids),
        (org_id, "guideline", org_id),
    ]
    if len(targets) > _MAX_REBUILD_TARGETS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"Rebuild is limited to {_MAX_REBUILD_TARGETS} sources per request.",
        )

    task_ids = await queue_embedding_tasks(
        db,
        targets,
        headers=correlation_headers(request),
    )
    return EmbeddingQueueOut(queued=len(task_ids))


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


    from app.services.embedding import get_embedding

    await _get_owned_org(org_id, current_user, db)

    query_vector = await get_embedding(q)


    with observe_operation("vector", "diagnostic_search"):
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
