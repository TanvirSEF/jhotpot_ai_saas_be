"""
Webhook Payload Parser — Phase A3 / A4 (Module A)

Meta sends all Page events through a single POST endpoint.
This module classifies each event entry into one of two types:

  1. MessengerEvent  — A private message sent to the Page inbox
  2. CommentEvent    — A public comment on a Page post

Both are extracted from Meta's standard webhook envelope:

  {
    "object": "page",
    "entry": [
      {
        "id": "<PAGE_ID>",
        "time": 1234567890,
        "messaging": [...],   // Messenger private messages
        "changes": [...]      // Feed/comment events
      }
    ]
  }

Design notes:
  - This is a pure data-transformation module; no DB access, no HTTP calls.
  - Returns typed dataclasses so the Celery worker and future unit tests
    can pattern-match on event type without inspecting raw dict keys.
  - Unknown or unsupported event types are logged and skipped (never crash
    the webhook pipeline on unexpected payloads).
  - Each function accepts the raw Meta dict, not a Pydantic model, to avoid
    double-parsing the body (we already parse JSON in the endpoint layer).
"""

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ── Typed event dataclasses ────────────────────────────────────────────────────

@dataclass
class MessengerEvent:
    """
    A private message sent to the Page inbox (Messenger).

    Fields:
        event_id    : Meta's stable message `mid`, used for deduplication.
        page_id     : The Facebook Page ID that received the message.
        sender_id   : PSID (Page-Scoped User ID) of the message sender.
        recipient_id: Page ID (same as page_id, included for completeness).
        message_text: Cleaned text content of the message.
        timestamp   : Unix ms timestamp from Meta.
        raw         : Original entry dict for debugging / future extensibility.
    """
    event_id: str
    page_id: str
    sender_id: str
    recipient_id: str
    message_text: str
    timestamp: int
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class CommentEvent:
    """
    A public comment on a Page post (feed subscription).

    Fields:
        event_id    : Meta's stable comment ID, used for deduplication.
        page_id     : The Facebook Page ID that owns the post.
        comment_id  : The Graph API ID of the new comment.
        post_id     : The Graph API ID of the parent post.
        sender_name : Display name of the commenter (may be None for privacy).
        comment_text: Text content of the comment.
        timestamp   : Unix ms timestamp from Meta.
        raw         : Original change dict for debugging.
    """
    event_id: str
    page_id: str
    comment_id: str
    post_id: str
    sender_name: str | None
    comment_text: str
    timestamp: int
    raw: dict = field(default_factory=dict, repr=False)


# Union type alias used in type hints elsewhere
WebhookEvent = MessengerEvent | CommentEvent


# ── Public parse function ──────────────────────────────────────────────────────

def parse_webhook_payload(payload: dict) -> list[WebhookEvent]:
    """
    Parse a Meta webhook POST body and return a flat list of typed events.

    Meta batches multiple entries (and multiple events per entry) in a single
    request. We flatten them into individual typed events so the Celery worker
    processes one event per task — enabling independent retry per event.

    Args:
        payload: The full deserialized JSON body from Meta.

    Returns:
        List of MessengerEvent or CommentEvent instances.
        Returns an empty list if the payload object is not "page" or if no
        recognized events are found (no exception raised).
    """
    if payload.get("object") != "page":
        logger.warning(
            "Received non-page webhook object: %s", payload.get("object")
        )
        return []

    events: list[WebhookEvent] = []

    for entry in payload.get("entry", []):
        page_id: str = entry.get("id", "")

        # ── Messenger private messages ─────────────────────────────────────────
        for messaging in entry.get("messaging", []):
            event = _parse_messaging_event(page_id, messaging)
            if event:
                events.append(event)

        # ── Feed / comment events ──────────────────────────────────────────────
        for change in entry.get("changes", []):
            if change.get("field") == "feed":
                event = _parse_feed_change(page_id, change)
                if event:
                    events.append(event)

    logger.debug("Parsed %d event(s) from webhook payload.", len(events))
    return events


# ── Private parsers ────────────────────────────────────────────────────────────

def _parse_messaging_event(
    page_id: str, messaging: dict
) -> MessengerEvent | None:
    """
    Extract a MessengerEvent from a single messaging entry.

    We only process text messages. Attachments, read receipts, typing
    indicators, and delivery confirmations are silently skipped — the bot
    cannot meaningfully respond to them.
    """
    message = messaging.get("message", {})

    # Skip non-text messages (images, stickers, echoes, etc.)
    if not message or message.get("is_echo"):
        return None

    event_id = str(message.get("mid", "")).strip()
    if not event_id:
        logger.warning("Messenger event has no provider message ID; skipping.")
        return None

    text = message.get("text", "").strip()
    if not text:
        logger.debug("Messenger event has no text content — skipping.")
        return None

    sender = messaging.get("sender", {})
    recipient = messaging.get("recipient", {})

    return MessengerEvent(
        event_id=event_id,
        page_id=page_id,
        sender_id=sender.get("id", ""),
        recipient_id=recipient.get("id", ""),
        message_text=text,
        timestamp=messaging.get("timestamp", 0),
        raw=messaging,
    )


def _parse_feed_change(
    page_id: str, change: dict
) -> CommentEvent | None:
    """
    Extract a CommentEvent from a feed change entry.

    We only handle new comments on Page posts (item=comment, verb=add).
    Post likes, shares, and edits are skipped.
    """
    value = change.get("value", {})

    # Only process new comments, not edits/removes/likes
    if value.get("item") != "comment" or value.get("verb") != "add":
        return None

    # Skip comments made by the Page itself (avoid reply loops)
    if value.get("from", {}).get("id") == page_id:
        logger.debug("Skipping self-comment from page %s", page_id)
        return None

    text = value.get("message", "").strip()
    if not text:
        logger.debug("Feed comment has no text content — skipping.")
        return None

    # post_id is embedded in comment_id: "<post_id>_<comment_id>"
    comment_id = value.get("comment_id", "")
    post_id = value.get("post_id", "")
    if not comment_id:
        logger.warning("Comment event has no provider comment ID; skipping.")
        return None

    return CommentEvent(
        event_id=comment_id,
        page_id=page_id,
        comment_id=comment_id,
        post_id=post_id,
        sender_name=value.get("from", {}).get("name"),
        comment_text=text,
        timestamp=value.get("created_time", 0),
        raw=change,
    )
