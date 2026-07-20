"""
Celery background tasks — NexusSuite AI

All tasks use `bind=True` so `self` gives access to retry machinery.
Database access inside Celery uses a synchronous SQLAlchemy session
(not async) because Celery workers run in a regular thread/process
context, not an asyncio event loop.

Task registry:
  • generate_embeddings  — OpenAI → pgvector write (Products, FAQs, Guidelines)
  • process_fb_webhook   — Meta webhook RAG pipeline (Phase A4)
  • export_resume_pdf    — WeasyPrint PDF compilation (Phase B)
"""

import asyncio
import logging
import uuid

from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# generate_embeddings
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="generate_embeddings",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
    acks_late=True,
)
def generate_embeddings(self, entity_type: str, entity_id: str) -> dict:
    """
    Fetch the source entity from DB, build the canonical text blob,
    call OpenAI text-embedding-3-small, then upsert into knowledge_embeddings.

    Args:
        entity_type: One of {"product", "faq", "guideline"}.
        entity_id:   UUID string of the source row.

    Returns:
        dict with status and embedding dimensionality for Celery result backend.
    """
    try:
        result = asyncio.run(_run_embedding(entity_type, entity_id))
        return result
    except Exception as exc:
        logger.error(
            "generate_embeddings failed [%s:%s]: %s",
            entity_type, entity_id, exc,
            exc_info=True,
        )
        raise self.retry(exc=exc)


async def _run_embedding(entity_type: str, entity_id: str) -> dict:
    """Async implementation called from the sync Celery task wrapper."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from sqlalchemy import select, delete

    from app.core.config import settings
    from app.models import Faq, KnowledgeEmbedding, Product
    from app.services.embedding import (
        get_embedding,
        build_product_text,
        build_faq_text,
    )

    entity_uuid = uuid.UUID(entity_id)

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with Session() as db:
        # ── 1. Fetch source entity ────────────────────────────────────────────
        if entity_type == "product":
            result = await db.execute(select(Product).where(Product.id == entity_uuid))
            entity = result.scalar_one_or_none()
            if not entity:
                logger.warning("Product %s not found; skipping embedding.", entity_id)
                return {"status": "skipped", "reason": "entity_not_found"}

            org_id = entity.org_id
            text = build_product_text({
                "name": entity.name,
                "sku": entity.sku,
                "price": float(entity.price),
                "stock_status": entity.stock_status.value,
                "description": entity.description,
            })
            metadata = {"product_name": entity.name, "sku": entity.sku}

        elif entity_type == "faq":
            result = await db.execute(select(Faq).where(Faq.id == entity_uuid))
            entity = result.scalar_one_or_none()
            if not entity:
                logger.warning("FAQ %s not found; skipping embedding.", entity_id)
                return {"status": "skipped", "reason": "entity_not_found"}

            org_id = entity.org_id
            text = build_faq_text(entity.question, entity.answer)
            metadata = {"question_preview": entity.question[:100]}

        else:
            logger.error("Unknown entity_type: %s", entity_type)
            return {"status": "error", "reason": f"unknown entity_type: {entity_type}"}

        # ── 2. Generate embedding ────────────────────────────────────────────
        logger.info("Generating embedding [%s:%s] text_len=%d", entity_type, entity_id, len(text))
        vector = await get_embedding(text)

        # ── 3. Remove stale embedding (idempotent upsert pattern) ────────────
        await db.execute(
            delete(KnowledgeEmbedding).where(
                KnowledgeEmbedding.org_id == org_id,
                KnowledgeEmbedding.entity_type == entity_type,
                KnowledgeEmbedding.entity_id == entity_uuid,
            )
        )

        # ── 4. Insert fresh embedding ────────────────────────────────────────
        embedding_row = KnowledgeEmbedding(
            org_id=org_id,
            content=text,
            embedding=vector,
            entity_type=entity_type,
            entity_id=entity_uuid,
            metadata_=metadata,
        )
        db.add(embedding_row)
        await db.commit()

        logger.info(
            "Embedding saved [%s:%s] dims=%d",
            entity_type, entity_id, len(vector),
        )
        return {"status": "ok", "entity_type": entity_type, "entity_id": entity_id, "dims": len(vector)}

    await engine.dispose()


# ──────────────────────────────────────────────────────────────────────────────
# process_fb_webhook  (Phase A3 / A4 — stub upgraded in next phase)
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="process_fb_webhook",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    acks_late=True,
)
def process_fb_webhook(self, payload: dict) -> dict:
    """
    Async RAG + Meta Graph API pipeline (Phase A4).
    Currently enqueues and logs; full implementation in Phase A4.
    """
    try:
        logger.info("Webhook payload received: %s", payload)
        # Phase A4: RAG retrieval + LLM call + Meta Graph API reply
        return {"status": "queued", "payload": payload}
    except Exception as exc:
        logger.error("process_fb_webhook failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)


# ──────────────────────────────────────────────────────────────────────────────
# export_resume_pdf  (Phase B stub)
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="export_resume_pdf",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
)
def export_resume_pdf(self, resume_id: str) -> dict:
    """WeasyPrint PDF compilation pipeline (Phase B)."""
    try:
        logger.info("PDF export queued for resume: %s", resume_id)
        return {"status": "queued", "resume_id": resume_id}
    except Exception as exc:
        logger.error("export_resume_pdf failed: %s", exc, exc_info=True)
        raise self.retry(exc=exc)
