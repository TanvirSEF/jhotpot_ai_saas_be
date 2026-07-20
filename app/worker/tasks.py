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
from collections.abc import Awaitable, Callable

from app.worker.celery_app import celery_app
from app.worker.db import task_db_session as _task_db_session
from app.worker.reliability import (
    PermanentTaskError,
    request_id_from_task,
    retry_or_fail,
    task_will_retry,
)

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
        result = asyncio.run(
            _run_embedding(entity_type, entity_id, task_id=str(self.request.id))
        )
        return result
    except Exception as exc:
        if not task_will_retry(self, exc):
            try:
                from app.services.embedding_status import mark_embedding_failed

                asyncio.run(
                    mark_embedding_failed(entity_type, entity_id, type(exc).__name__)
                )
            except Exception:
                logger.exception(
                    "Could not mark embedding status failed [%s:%s]",
                    entity_type,
                    entity_id,
                )
        retry_or_fail(
            self,
            exc,
            base_delay=10,
            safe_context={"entity_type": entity_type, "entity_id": entity_id},
        )


async def _run_embedding(
    entity_type: str,
    entity_id: str,
    *,
    task_id: str | None = None,
) -> dict:
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
    from app.models.embedding_status import EmbeddingJobState
    from app.services.embedding_status import content_digest, set_embedding_status

    entity_uuid = uuid.UUID(entity_id)

    async with _task_db_session() as db:
        # ── 1. Fetch source entity ────────────────────────────────────────────
        if entity_type == "product":
            result = await db.execute(select(Product).where(Product.id == entity_uuid))
            entity = result.scalar_one_or_none()
            if not entity:
                raise PermanentTaskError("Embedding source does not exist.")

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
                raise PermanentTaskError("Embedding source does not exist.")

            org_id = entity.org_id
            text = build_faq_text(entity.question, entity.answer)
            metadata = {"question_preview": entity.question[:100]}

        elif entity_type == "guideline":
            result = await db.execute(
                select(Organization).where(Organization.id == entity_uuid)
            )
            entity = result.scalar_one_or_none()
            if not entity:
                raise PermanentTaskError("Embedding source does not exist.")

            if not entity.global_guidelines or not entity.global_guidelines.strip():
                await db.execute(
                    delete(KnowledgeEmbedding).where(
                        KnowledgeEmbedding.org_id == entity.id,
                        KnowledgeEmbedding.entity_type == "guideline",
                        KnowledgeEmbedding.entity_id == entity.id,
                    )
                )
                await set_embedding_status(
                    db,
                    org_id=entity.id,
                    entity_type="guideline",
                    entity_id=entity.id,
                    state=EmbeddingJobState.NOT_REQUIRED,
                    task_id=task_id,
                )
                await db.commit()
                return {"status": "skipped", "reason": "guideline_is_empty"}

            org_id = entity.id
            text = build_guideline_text(entity.global_guidelines)
            metadata = {"business_name": entity.business_name}

        else:
            raise PermanentTaskError("Unsupported embedding entity type.")

        # ── 2. Generate embedding ────────────────────────────────────────────
        await set_embedding_status(
            db,
            org_id=org_id,
            entity_type=entity_type,
            entity_id=entity_uuid,
            state=EmbeddingJobState.PROCESSING,
            task_id=task_id,
        )
        await db.commit()
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
        await set_embedding_status(
            db,
            org_id=org_id,
            entity_type=entity_type,
            entity_id=entity_uuid,
            state=EmbeddingJobState.READY,
            task_id=task_id,
            content_hash=content_digest(text),
        )
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
def process_fb_webhook(self, event_ref: str | dict) -> dict:
    """
    Full RAG + Meta Graph API pipeline for a single classified webhook event.

    Called by the FastAPI webhook endpoint (Phase A3) once per event.
    Each event is processed independently so retries don't affect siblings.

    Args:
        event_ref: Durable webhook inbox UUID. A normalized event dict remains
                   accepted temporarily for rolling-deploy compatibility.

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
    legacy_event = event_ref if isinstance(event_ref, dict) else None
    inbox_event_id = None if legacy_event is not None else str(event_ref)
    try:
        logger.info(
            "Webhook task started task_id=%s request_id=%s inbox_event=%s",
            self.request.id,
            request_id_from_task(self),
            inbox_event_id,
        )
        if legacy_event is not None:
            # Rolling-deploy compatibility for tasks published before Phase 7.
            return asyncio.run(_run_webhook_pipeline(legacy_event))
        return asyncio.run(_run_inbox_webhook_pipeline(inbox_event_id))
    except Exception as exc:
        if inbox_event_id:
            try:
                asyncio.run(
                    _mark_inbox_attempt_failed(
                        inbox_event_id,
                        retrying=task_will_retry(self, exc),
                        error_code=type(exc).__name__,
                    )
                )
            except Exception:
                logger.exception(
                    "Could not update webhook inbox failure event=%s",
                    inbox_event_id,
                )
        retry_or_fail(
            self,
            exc,
            base_delay=5,
            safe_context={
                "inbox_event_id": inbox_event_id,
                "legacy_event_type": (
                    str(legacy_event.get("type", ""))[:50]
                    if legacy_event
                    else None
                ),
            },
        )


async def _run_inbox_webhook_pipeline(event_id: str) -> dict:
    """Claim one durable event, run it once, and persist its terminal state."""
    from app.services.webhook_inbox import (
        claim_webhook_event,
        transition_webhook_event,
    )

    try:
        parsed_id = uuid.UUID(event_id)
    except (TypeError, ValueError) as exc:
        raise PermanentTaskError("Invalid webhook inbox event ID.") from exc

    async with _task_db_session() as db:
        inbox_event = await claim_webhook_event(db, parsed_id)
    if inbox_event is None:
        return {"status": "skipped", "reason": "already_claimed_or_complete"}

    async def mark_delivering() -> None:
        async with _task_db_session() as db:
            await transition_webhook_event(db, parsed_id, state="delivering")

    result = await _run_webhook_pipeline(
        inbox_event.payload,
        before_delivery=mark_delivering,
        webhook_event_id=parsed_id,
    )
    terminal_state = "succeeded" if result.get("status") == "ok" else "skipped"
    async with _task_db_session() as db:
        await transition_webhook_event(db, parsed_id, state=terminal_state)
    return {**result, "inbox_event_id": event_id}


async def _mark_inbox_attempt_failed(
    event_id: str,
    *,
    retrying: bool,
    error_code: str,
) -> None:
    from app.services.webhook_inbox import transition_webhook_event

    try:
        parsed_id = uuid.UUID(event_id)
    except (TypeError, ValueError):
        return
    async with _task_db_session() as db:
        await transition_webhook_event(
            db,
            parsed_id,
            state="retrying" if retrying else "failed",
            error_code=error_code[:100],
        )


async def _run_webhook_pipeline(
    event_dict: dict,
    *,
    before_delivery: Callable[[], Awaitable[None]] | None = None,
    webhook_event_id: uuid.UUID | None = None,
) -> dict:
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

        if (
            fb_page.connection_status != "connected"
            or fb_page.subscription_status != "subscribed"
            or fb_page.token_status != "valid"
        ):
            logger.warning(
                "Page lifecycle is not ready page=%s connection=%s subscription=%s token=%s",
                page_id,
                fb_page.connection_status,
                fb_page.subscription_status,
                fb_page.token_status,
            )
            return {"status": "skipped", "reason": "page_not_ready"}

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
        rag_result = await run_rag_pipeline(
            db=db,
            org_id=fb_page.org_id,
            business_name=business_name,
            guidelines=guidelines,
            customer_message=customer_message,
        )
        if webhook_event_id is not None:
            from app.services.rag_audit import record_rag_run

            await record_rag_run(
                db,
                org_id=fb_page.org_id,
                webhook_event_id=webhook_event_id,
                result=rag_result,
            )
        ai_reply = rag_result.reply

        # ── 10. Send reply via Meta Graph API ────────────────────────────────
        # This state is intentionally not auto-recovered. If a worker dies
        # around the external call, replaying blindly could send two replies.
        if before_delivery is not None:
            await before_delivery()

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
            "rag_outcome": rag_result.outcome,
            "rag_fallback_reason": rag_result.fallback_reason,
            "rag_prompt_version": rag_result.prompt_version,
            "rag_total_tokens": rag_result.total_tokens,
        }


# ──────────────────────────────────────────────────────────────────────────────
# export_resume_pdf  (Phase B3)
# ──────────────────────────────────────────────────────────────────────────────

@celery_app.task(
    name="recover_fb_webhook_inbox",
    soft_time_limit=30,
    time_limit=45,
)
def recover_fb_webhook_inbox() -> dict:
    """Republish committed events left behind by broker or worker failures."""
    return asyncio.run(_recover_fb_webhook_inbox())


async def _recover_fb_webhook_inbox() -> dict:
    from app.services.webhook_inbox import (
        mark_webhook_queued,
        recovery_candidates,
    )

    async with _task_db_session() as db:
        candidates = await recovery_candidates(db)
        queued = 0
        for event in candidates:
            task_id = str(uuid.uuid4())
            headers = {"request_id": event.request_id} if event.request_id else {}
            try:
                process_fb_webhook.apply_async(
                    args=(str(event.id),),
                    task_id=task_id,
                    headers=headers,
                )
            except Exception as exc:
                logger.warning(
                    "Webhook inbox recovery publish failed event=%s error=%s",
                    event.id,
                    type(exc).__name__,
                )
                continue
            await mark_webhook_queued(db, event.id, task_id)
            queued += 1
        await db.commit()
    return {"status": "ok", "candidates": len(candidates), "queued": queued}


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
