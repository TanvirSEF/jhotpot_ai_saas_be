"""Transactional inbox primitives for Meta webhook idempotency and recovery."""

import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import FbPage, WebhookEvent
from app.services.webhook_parser import WebhookEvent as ParsedWebhookEvent

CLAIMABLE_STATES = ("accepted", "queued", "retrying")
TERMINAL_STATES = ("succeeded", "skipped", "failed")
RECOVERY_BATCH_SIZE = 100
PROCESSING_LEASE_MINUTES = 5


@dataclass(frozen=True)
class AcceptedWebhookEvent:
    id: uuid.UUID
    request_id: str | None


@dataclass(frozen=True)
class InboxWriteResult:
    accepted: list[AcceptedWebhookEvent]
    duplicates: int
    unregistered: int


def normalized_payload(event: ParsedWebhookEvent) -> dict:
    """Create the bounded worker payload; never persist Meta's raw envelope."""
    payload = asdict(event)
    payload.pop("raw", None)
    payload["type"] = type(event).__name__
    return payload


async def persist_webhook_events(
    db: AsyncSession,
    events: list[ParsedWebhookEvent],
    *,
    request_id: str | None,
) -> InboxWriteResult:
    """Atomically insert recognized events and ignore provider redeliveries."""
    page_ids = {event.page_id for event in events if event.page_id}
    pages_by_external_id: dict[str, FbPage] = {}
    if page_ids:
        result = await db.execute(select(FbPage).where(FbPage.page_id.in_(page_ids)))
        pages_by_external_id = {page.page_id: page for page in result.scalars()}

    accepted: list[AcceptedWebhookEvent] = []
    duplicates = 0
    unregistered = 0
    safe_request_id = str(request_id)[:255] if request_id else None

    for event in events:
        page = pages_by_external_id.get(event.page_id)
        if page is None:
            unregistered += 1
            continue

        event_id = uuid.uuid4()
        statement = (
            insert(WebhookEvent)
            .values(
                id=event_id,
                org_id=page.org_id,
                fb_page_id=page.id,
                provider_event_id=event.event_id[:255],
                event_type=type(event).__name__,
                state="accepted",
                payload=normalized_payload(event),
                event_timestamp=int(event.timestamp or 0),
                request_id=safe_request_id,
                attempts=0,
            )
            .on_conflict_do_nothing(
                constraint="uq_webhook_events_provider_event"
            )
            .returning(WebhookEvent.id)
        )
        inserted_id = (await db.execute(statement)).scalar_one_or_none()
        if inserted_id is None:
            duplicates += 1
        else:
            accepted.append(
                AcceptedWebhookEvent(id=inserted_id, request_id=safe_request_id)
            )

    await db.commit()
    return InboxWriteResult(
        accepted=accepted,
        duplicates=duplicates,
        unregistered=unregistered,
    )


async def mark_webhook_queued(
    db: AsyncSession,
    event_id: uuid.UUID,
    task_id: str,
) -> None:
    """Record successful publication without overwriting a fast worker."""
    now = datetime.now(timezone.utc)
    await db.execute(
        update(WebhookEvent)
        .where(
            WebhookEvent.id == event_id,
            WebhookEvent.state.in_(("accepted", "retrying")),
        )
        .values(state="queued", celery_task_id=task_id[:255], queued_at=now)
    )


async def claim_webhook_event(
    db: AsyncSession,
    event_id: uuid.UUID,
) -> WebhookEvent | None:
    """Atomically grant one worker the right to process an inbox event."""
    now = datetime.now(timezone.utc)
    result = await db.execute(
        update(WebhookEvent)
        .where(
            WebhookEvent.id == event_id,
            WebhookEvent.state.in_(CLAIMABLE_STATES),
        )
        .values(
            state="processing",
            attempts=WebhookEvent.attempts + 1,
            processing_started_at=now,
            last_error_code=None,
        )
        .returning(WebhookEvent)
    )
    event = result.scalar_one_or_none()
    await db.commit()
    return event


async def transition_webhook_event(
    db: AsyncSession,
    event_id: uuid.UUID,
    *,
    state: str,
    error_code: str | None = None,
) -> None:
    """Persist one explicit lifecycle transition using sanitized metadata."""
    now = datetime.now(timezone.utc)
    values: dict = {"state": state, "last_error_code": error_code}
    if state == "delivering":
        values["delivery_started_at"] = now
    if state in TERMINAL_STATES:
        values["completed_at"] = now
    await db.execute(
        update(WebhookEvent).where(WebhookEvent.id == event_id).values(**values)
    )
    await db.commit()


async def recovery_candidates(
    db: AsyncSession,
    *,
    limit: int = RECOVERY_BATCH_SIZE,
) -> list[AcceptedWebhookEvent]:
    """Release abandoned pre-delivery claims and return publishable events."""
    now = datetime.now(timezone.utc)
    stale_before = now - timedelta(minutes=PROCESSING_LEASE_MINUTES)
    await db.execute(
        update(WebhookEvent)
        .where(
            WebhookEvent.state == "processing",
            WebhookEvent.processing_started_at < stale_before,
        )
        .values(
            state="accepted",
            last_error_code="processing_lease_expired",
            processing_started_at=None,
        )
    )
    result = await db.execute(
        select(WebhookEvent.id, WebhookEvent.request_id)
        .where(
            (WebhookEvent.state == "accepted")
            | (
                (WebhookEvent.state == "queued")
                & (WebhookEvent.updated_at < stale_before)
            )
            | (
                (WebhookEvent.state == "retrying")
                & (WebhookEvent.updated_at < stale_before)
            )
        )
        .order_by(WebhookEvent.received_at)
        .limit(max(1, min(limit, RECOVERY_BATCH_SIZE)))
    )
    candidates = [
        AcceptedWebhookEvent(id=row.id, request_id=row.request_id)
        for row in result
    ]
    await db.commit()
    return candidates
