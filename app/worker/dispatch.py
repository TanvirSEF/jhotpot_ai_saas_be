"""Durable preparation and dispatch helpers for embedding tasks."""

import uuid
from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.embedding_status import EmbeddingJobState
from app.services.embedding_status import set_embedding_status


async def queue_embedding_tasks(
    db: AsyncSession,
    targets: Iterable[tuple[uuid.UUID, str, uuid.UUID]],
    *,
    headers: dict[str, str] | None = None,
) -> list[str]:
    """Persist pending states before publishing tasks to Redis."""
    from app.worker.tasks import generate_embeddings

    prepared: list[tuple[str, str, uuid.UUID]] = []
    for org_id, entity_type, entity_id in targets:
        task_id = str(uuid.uuid4())
        await set_embedding_status(
            db,
            org_id=org_id,
            entity_type=entity_type,
            entity_id=entity_id,
            state=EmbeddingJobState.PENDING,
            task_id=task_id,
        )
        prepared.append((task_id, entity_type, entity_id))

    await db.commit()

    for task_id, entity_type, entity_id in prepared:
        generate_embeddings.apply_async(
            args=(entity_type, str(entity_id)),
            task_id=task_id,
            headers=headers or {},
        )
    return [task_id for task_id, _, _ in prepared]
