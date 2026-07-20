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
        # Exponential backoff: 10s → 20s → 40s
        # Prevents hammering OpenAI API during rate-limit windows
        backoff = 10 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=backoff)


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
# process_fb_webhook  — Full RAG Pipeline (Phase A4)
# ──────────────────────────────────────────────────────────────────────────────

_TWENTY_FOUR_HOURS_S = 86_400   # Meta standard messaging window (seconds)


@celery_app.task(
    name="process_fb_webhook",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
    acks_late=True,
)
def process_fb_webhook(self, event_dict: dict) -> dict:
    """
    Full RAG + Meta Graph API pipeline for a single classified webhook event.

    Called by the FastAPI webhook endpoint (Phase A3) once per event.
    Each event is processed independently so retries don't affect siblings.

    Args:
        event_dict: Serialised MessengerEvent or CommentEvent dict produced
                    by webhook_parser.parse_webhook_payload().

    Pipeline:
      1. Identify event type (MessengerEvent | CommentEvent)
      2. Look up fb_pages row → get org_id, is_bot_active, encrypted token
      3. Guard: skip if bot is inactive for this page
      4. Guard: 24-hour messaging window (Messenger only, per PRD §6.2)
      5. Decrypt Page Access Token
      6. Fetch org business_name + global_guidelines
      7. RAG retrieval (pgvector cosine search, top 4 chunks)
      8. Build system prompt with guardrails
      9. Call OpenAI LLM (gpt-4o-mini)
      10. Send reply via Meta Graph API

    Returns:
        Status dict stored in Celery result backend.
    """
    try:
        result = asyncio.run(_run_webhook_pipeline(event_dict))
        return result
    except Exception as exc:
        logger.error(
            "process_fb_webhook failed [type=%s]: %s",
            event_dict.get("type"), exc,
            exc_info=True,
        )
        # Exponential backoff: 5s → 10s → 20s
        # Prevents hammering Meta Graph API during transient failures
        backoff = 5 * (2 ** self.request.retries)
        raise self.retry(exc=exc, countdown=backoff)


async def _run_webhook_pipeline(event_dict: dict) -> dict:
    """Async implementation — called from the sync Celery wrapper."""
    import time
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from app.core.config import settings
    from app.core.security import decrypt_token
    from app.models import FbPage, Organization
    from app.services.rag import run_rag_pipeline
    from app.services.graph_api import send_messenger_reply, post_comment_reply

    event_type = event_dict.get("type")
    page_id = event_dict.get("page_id", "")

    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with Session() as db:
        # ── 1. Look up the Facebook Page record ──────────────────────────────
        result = await db.execute(
            select(FbPage).where(FbPage.page_id == page_id)
        )
        fb_page = result.scalar_one_or_none()

        if not fb_page:
            logger.warning("No fb_pages record found for page_id=%s — skipping.", page_id)
            return {"status": "skipped", "reason": "page_not_registered"}

        # ── 2. Guard: bot must be active for this page ───────────────────────
        if not fb_page.is_bot_active:
            logger.info("Bot is inactive for page=%s — skipping.", page_id)
            return {"status": "skipped", "reason": "bot_inactive"}

        # ── 3. 24-hour messaging window check (Messenger only) ────────────────
        if event_type == "MessengerEvent":
            event_timestamp_ms = event_dict.get("timestamp", 0)
            elapsed_seconds = time.time() - (event_timestamp_ms / 1000)
            if elapsed_seconds > _TWENTY_FOUR_HOURS_S:
                logger.warning(
                    "24h window expired for page=%s sender=%s (elapsed=%.0fs) — skipping.",
                    page_id, event_dict.get("sender_id"), elapsed_seconds,
                )
                return {"status": "skipped", "reason": "24h_window_expired"}

        # ── 4. Decrypt Page Access Token ──────────────────────────────────────
        try:
            plain_token = decrypt_token(fb_page.encrypted_access_token)
        except Exception as exc:
            logger.error("Token decryption failed for page=%s: %s", page_id, exc)
            return {"status": "error", "reason": "token_decryption_failed"}

        # ── 5. Fetch org business name + guidelines ───────────────────────────
        org_result = await db.execute(
            select(Organization).where(Organization.id == fb_page.org_id)
        )
        org = org_result.scalar_one_or_none()

        if not org:
            logger.error("Organisation not found for org_id=%s", fb_page.org_id)
            return {"status": "error", "reason": "org_not_found"}

        business_name = org.business_name
        guidelines = org.global_guidelines

        # ── 6. Determine customer message text ───────────────────────────────
        if event_type == "MessengerEvent":
            customer_message = event_dict.get("message_text", "").strip()
        elif event_type == "CommentEvent":
            customer_message = event_dict.get("comment_text", "").strip()
        else:
            logger.warning("Unknown event type: %s — skipping.", event_type)
            return {"status": "skipped", "reason": f"unknown_event_type: {event_type}"}

        if not customer_message:
            logger.debug("Empty customer message — skipping.")
            return {"status": "skipped", "reason": "empty_message"}

        logger.info(
            "RAG pipeline starting [%s] page=%s org=%s query_len=%d",
            event_type, page_id, org.id, len(customer_message),
        )

        # ── 7-9. RAG retrieval + LLM generation ──────────────────────────────
        ai_reply = await run_rag_pipeline(
            db=db,
            org_id=fb_page.org_id,
            business_name=business_name,
            guidelines=guidelines,
            customer_message=customer_message,
        )

        # ── 10. Send reply via Meta Graph API ────────────────────────────────
        if event_type == "MessengerEvent":
            sender_psid = event_dict.get("sender_id", "")
            success = await send_messenger_reply(plain_token, sender_psid, ai_reply)
            reply_target = f"messenger:{sender_psid}"

        else:  # CommentEvent
            comment_id = event_dict.get("comment_id", "")
            success = await post_comment_reply(plain_token, comment_id, ai_reply)
            reply_target = f"comment:{comment_id}"

        status = "ok" if success else "reply_failed"
        logger.info(
            "RAG pipeline complete [%s] page=%s target=%s status=%s",
            event_type, page_id, reply_target, status,
        )

        return {
            "status": status,
            "event_type": event_type,
            "page_id": page_id,
            "reply_target": reply_target,
            "reply_length": len(ai_reply),
        }

    await engine.dispose()



# ──────────────────────────────────────────────────────────────────────────────
# export_resume_pdf  (Phase B3)
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="export_resume_pdf",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
)
def export_resume_pdf(self, resume_id: str) -> dict:
    """
    Background WeasyPrint PDF compilation pipeline (Phase B3).
    Fetches resume row, compiles PDF bytes, and returns metadata.
    """
    try:
        result = asyncio.run(_run_export_resume_pdf(resume_id))
        return result
    except Exception as exc:
        logger.error("export_resume_pdf failed [%s]: %s", resume_id, exc, exc_info=True)
        raise self.retry(exc=exc)


async def _run_export_resume_pdf(resume_id: str) -> dict:
    """Async wrapper for export_resume_pdf task."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
    from app.core.config import settings
    from app.models import Resume
    from app.services.pdf_generator import generate_resume_pdf

    resume_uuid = uuid.UUID(resume_id)
    engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
    Session = async_sessionmaker(bind=engine, expire_on_commit=False)

    async with Session() as db:
        res = await db.execute(select(Resume).where(Resume.id == resume_uuid))
        resume = res.scalar_one_or_none()

        if not resume:
            logger.warning("Resume %s not found for PDF compilation.", resume_id)
            return {"status": "error", "reason": "resume_not_found"}

        data = resume.optimized_json_data or resume.raw_json_data
        pdf_bytes = generate_resume_pdf(data)

        return {
            "status": "ok",
            "resume_id": resume_id,
            "pdf_size_bytes": len(pdf_bytes),
        }

    await engine.dispose()

