"""Persistence boundary for content-free RAG usage and grounding metrics."""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import RagRun
from app.services.rag import RagResult


async def record_rag_run(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    webhook_event_id: uuid.UUID,
    result: RagResult,
) -> None:
    """Record one attempt without storing prompts, messages, sources, or replies."""
    db.add(
        RagRun(
            org_id=org_id,
            webhook_event_id=webhook_event_id,
            model=result.model[:100],
            prompt_version=result.prompt_version[:50],
            outcome=result.outcome,
            fallback_reason=(result.fallback_reason or None),
            retrieval_count=result.retrieval_count,
            top_similarity=result.top_similarity,
            prompt_tokens=result.prompt_tokens,
            completion_tokens=result.completion_tokens,
            total_tokens=result.total_tokens,
            reply_length=len(result.reply),
        )
    )
    await db.commit()
