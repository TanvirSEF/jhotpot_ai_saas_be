"""Atomic state transitions for knowledge-base embedding generation."""

import hashlib
import uuid

from sqlalchemy import delete, func, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.embedding_status import EmbeddingJobState, EmbeddingStatusRecord
from app.worker.db import task_db_session


def content_digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


async def set_embedding_status(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
    state: EmbeddingJobState,
    task_id: str | None = None,
    content_hash: str | None = None,
    error_code: str | None = None,
) -> None:
    """Upsert one state transition without creating duplicate status rows."""
    insert_attempts = 1 if state is EmbeddingJobState.PROCESSING else 0
    statement = insert(EmbeddingStatusRecord).values(
        id=uuid.uuid4(),
        org_id=org_id,
        entity_type=entity_type,
        entity_id=entity_id,
        state=state.value,
        task_id=task_id,
        attempts=insert_attempts,
        content_hash=content_hash,
        last_error_code=error_code,
    )

    updates: dict = {
        "state": state.value,
        "updated_at": func.now(),
    }
    if state is EmbeddingJobState.PENDING:
        updates.update(task_id=task_id, attempts=0, last_error_code=None)
    elif state is EmbeddingJobState.PROCESSING:
        updates.update(
            task_id=task_id,
            attempts=EmbeddingStatusRecord.attempts + 1,
            last_error_code=None,
        )
    elif state is EmbeddingJobState.READY:
        updates.update(task_id=task_id, content_hash=content_hash, last_error_code=None)
    elif state is EmbeddingJobState.FAILED:
        updates.update(last_error_code=(error_code or "TaskError")[:100])
    elif state is EmbeddingJobState.NOT_REQUIRED:
        updates.update(task_id=task_id, content_hash=None, last_error_code=None)

    statement = statement.on_conflict_do_update(
        constraint="uq_embedding_statuses_entity",
        set_=updates,
    )
    await db.execute(statement)


async def delete_embedding_status(
    db: AsyncSession,
    *,
    org_id: uuid.UUID,
    entity_type: str,
    entity_id: uuid.UUID,
) -> None:
    await db.execute(
        delete(EmbeddingStatusRecord).where(
            EmbeddingStatusRecord.org_id == org_id,
            EmbeddingStatusRecord.entity_type == entity_type,
            EmbeddingStatusRecord.entity_id == entity_id,
        )
    )


async def mark_embedding_failed(
    entity_type: str,
    entity_id: str,
    error_code: str,
) -> None:
    """Mark a terminal task failure without storing provider error details."""
    try:
        entity_uuid = uuid.UUID(entity_id)
    except ValueError:
        return

    async with task_db_session() as db:
        await db.execute(
            update(EmbeddingStatusRecord)
            .where(
                EmbeddingStatusRecord.entity_type == entity_type,
                EmbeddingStatusRecord.entity_id == entity_uuid,
            )
            .values(
                state=EmbeddingJobState.FAILED.value,
                last_error_code=error_code[:100],
                updated_at=func.now(),
            )
        )
        await db.commit()
