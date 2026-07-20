"""
Celery background tasks — NexusSuite AI

All tasks use `bind=True` so `self` gives access to retry machinery. Celery
entry points are synchronous and use `asyncio.run()` around their async
implementations. Each task owns an async SQLAlchemy engine and reliably
disposes it before returning.

Task registry:
  • generate_embeddings  — OpenAI → pgvector write (Products, FAQs, Guidelines)
  • process_fb_webhook   — Meta webhook RAG pipeline (Phase A4)
  • export_resume_pdf    — WeasyPrint PDF compilation (Phase B)
"""

import asyncio
import logging
import uuid

from app.worker.celery_app import celery_app
from app.worker.db import task_db_session as _task_db_session
from app.worker.reliability import PermanentTaskError, request_id_from_task, retry_or_fail

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
    soft_time_limit=45,
    time_limit=60,
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
        retry_or_fail(
            self,
            exc,
            base_delay=10,
            safe_context={"entity_type": entity_type, "entity_id": entity_id},
        )


async def _run_embedding(entity_type: str, entity_id: str) -> dict:
    """Async implementation called from the sync Celery task wrapper."""
    from sqlalchemy import delete, func, select
    from sqlalchemy.dialects.postgresql import insert

    from app.models import Faq, KnowledgeEmbedding, Organization, Product
    from app.services.embedding import (
        get_embedding,
        build_product_text,
        build_faq_text,
        build_guideline_text,
    )

    entity_uuid = uuid.UUID(entity_id)

    async with _task_db_session() as db:
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
                "category": entity.category,
                "attributes": entity.attributes,
                "price": float(entity.price),
                "stock_status": entity.stock_status.value,
                "description": entity.description,
            })
            metadata = {
                "product_name": entity.name,
                "sku": entity.sku,
                "category": entity.category,
            }

        elif entity_type == "faq":
            result = await db.execute(select(Faq).where(Faq.id == entity_uuid))
            entity = result.scalar_one_or_none()
            if not entity:
                logger.warning("FAQ %s not found; skipping embedding.", entity_id)
                return {"status": "skipped", "reason": "entity_not_found"}

            org_id = entity.org_id
            text = build_faq_text(entity.question, entity.answer)
            metadata = {"question_preview": entity.question[:100]}

        elif entity_type == "guideline":
            result = await db.execute(
                select(Organization).where(Organization.id == entity_uuid)
            )
            entity = result.scalar_one_or_none()
            if not entity:
                logger.warning(
                    "Organization %s not found; skipping guideline embedding.",
                    entity_id,
                )
                return {"status": "skipped", "reason": "entity_not_found"}

            if not entity.global_guidelines or not entity.global_guidelines.strip():
                await db.execute(
                    delete(KnowledgeEmbedding).where(
                        KnowledgeEmbedding.org_id == entity.id,
                        KnowledgeEmbedding.entity_type == "guideline",
                        KnowledgeEmbedding.entity_id == entity.id,
                    )
                )
                await db.commit()
                return {"status": "skipped", "reason": "guideline_is_empty"}

            org_id = entity.id
            text = build_guideline_text(entity.global_guidelines)
            metadata = {"business_name": entity.business_name}

        else:
            raise PermanentTaskError("Unsupported embedding entity type.")

        # ── 2. Generate embedding ────────────────────────────────────────────
        logger.info("Generating embedding [%s:%s] text_len=%d", entity_type, entity_id, len(text))
        vector = await get_embedding(text)

        # One statement preserves the old vector until the replacement commits.
        statement = insert(KnowledgeEmbedding).values(
            id=uuid.uuid4(),
            org_id=org_id,
            content=text,
            embedding=vector,
            entity_type=entity_type,
            entity_id=entity_uuid,
            metadata=metadata,
        )
        statement = statement.on_conflict_do_update(
            constraint="uq_knowledge_embeddings_entity",
            set_={
                "content": statement.excluded.content,
                "embedding": statement.excluded.embedding,
                "metadata": statement.excluded.metadata,
                "created_at": func.now(),
            },
        )
        await db.execute(statement)
        await db.commit()

        logger.info(
            "Embedding saved [%s:%s] dims=%d",
            entity_type, entity_id, len(vector),
        )
        return {
            "status": "ok",
            "entity_type": entity_type,
            "entity_id": entity_id,
            "dims": len(vector),
        }

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
    soft_time_limit=60,
    time_limit=75,
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
        logger.info(
            "Webhook task started task_id=%s request_id=%s type=%s page_id=%s",
            self.request.id,
            request_id_from_task(self),
            event_dict.get("type"),
            event_dict.get("page_id"),
        )
        result = asyncio.run(_run_webhook_pipeline(event_dict))
        return result
    except Exception as exc:
        retry_or_fail(
            self,
            exc,
            base_delay=5,
            safe_context={
                "event_type": str(event_dict.get("type", ""))[:50],
                "page_id": str(event_dict.get("page_id", ""))[:255],
            },
        )


async def _run_webhook_pipeline(event_dict: dict) -> dict:
    """Async implementation — called from the sync Celery wrapper."""
    import time
    from sqlalchemy import select

    from app.core.security import decrypt_token
    from app.models import FbPage, Organization
    from app.services.rag import run_rag_pipeline
    from app.services.graph_api import send_messenger_reply, post_comment_reply

    event_type = event_dict.get("type")
    page_id = event_dict.get("page_id", "")

    async with _task_db_session() as db:
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
            logger.error("Token decryption failed for page=%s", page_id)
            raise PermanentTaskError("Stored Page token could not be decrypted.") from exc

        # ── 5. Fetch org business name + guidelines ───────────────────────────
        org_result = await db.execute(
            select(Organization).where(Organization.id == fb_page.org_id)
        )
        org = org_result.scalar_one_or_none()

        if not org:
            raise PermanentTaskError("Page organization does not exist.")

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
            await send_messenger_reply(plain_token, sender_psid, ai_reply)
            reply_target = f"messenger:{sender_psid}"

        else:  # CommentEvent
            comment_id = event_dict.get("comment_id", "")
            await post_comment_reply(plain_token, comment_id, ai_reply)
            reply_target = f"comment:{comment_id}"

        status = "ok"
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


# ──────────────────────────────────────────────────────────────────────────────
# export_resume_pdf  (Phase B3)
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="export_resume_pdf",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
    acks_late=True,
    soft_time_limit=90,
    time_limit=120,
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
        retry_or_fail(
            self,
            exc,
            base_delay=15,
            safe_context={"resume_id": resume_id},
        )


async def _run_export_resume_pdf(resume_id: str) -> dict:
    """Async wrapper for export_resume_pdf task."""
    from sqlalchemy import select
    from app.models import Resume
    from app.services.pdf_generator import generate_resume_pdf

    resume_uuid = uuid.UUID(resume_id)

    async with _task_db_session() as db:
        res = await db.execute(select(Resume).where(Resume.id == resume_uuid))
        resume = res.scalar_one_or_none()

        if not resume:
            logger.warning("Resume %s not found for PDF compilation.", resume_id)
            raise PermanentTaskError("Resume does not exist.")

        data = resume.optimized_json_data or resume.raw_json_data
        pdf_bytes = generate_resume_pdf(data)

        return {
            "status": "ok",
            "resume_id": resume_id,
            "pdf_size_bytes": len(pdf_bytes),
        }
