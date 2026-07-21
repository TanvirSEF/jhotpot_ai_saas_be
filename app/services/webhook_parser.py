


import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class MessengerEvent:


    event_id: str
    page_id: str
    sender_id: str
    recipient_id: str
    message_text: str
    timestamp: int
    raw: dict = field(default_factory=dict, repr=False)


@dataclass
class CommentEvent:


    event_id: str
    page_id: str
    comment_id: str
    post_id: str
    sender_name: str | None
    comment_text: str
    timestamp: int
    raw: dict = field(default_factory=dict, repr=False)


WebhookEvent = MessengerEvent | CommentEvent


def parse_webhook_payload(payload: dict) -> list[WebhookEvent]:


    if payload.get("object") != "page":
        logger.warning(
            "Received non-page webhook object: %s", payload.get("object")
        )
        return []

    events: list[WebhookEvent] = []

    for entry in payload.get("entry", []):
        page_id: str = entry.get("id", "")


        for messaging in entry.get("messaging", []):
            messenger_event = _parse_messaging_event(page_id, messaging)
            if messenger_event:
                events.append(messenger_event)


        for change in entry.get("changes", []):
            if change.get("field") == "feed":
                comment_event = _parse_feed_change(page_id, change)
                if comment_event:
                    events.append(comment_event)

    logger.debug("Parsed %d event(s) from webhook payload.", len(events))
    return events


def _parse_messaging_event(
    page_id: str, messaging: dict
) -> MessengerEvent | None:


    message = messaging.get("message", {})


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


    value = change.get("value", {})


    if value.get("item") != "comment" or value.get("verb") != "add":
        return None


    if value.get("from", {}).get("id") == page_id:
        logger.debug("Skipping self-comment from page %s", page_id)
        return None

    text = value.get("message", "").strip()
    if not text:
        logger.debug("Feed comment has no text content — skipping.")
        return None


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
