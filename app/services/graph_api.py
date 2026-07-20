"""
Meta Graph API Reply Service — Phase A4

Handles the final step of the RAG pipeline: sending the AI-generated
reply back to the customer via the Meta Graph API.

Supports two reply modes:
  1. Messenger private message (MessengerEvent) — POST /me/messages
  2. Public page comment reply (CommentEvent)   — POST /{comment_id}/comments

Design decisions:
  - Each function creates a fresh httpx.AsyncClient per call. Graph API
    calls are infrequent (one per webhook event) so persistent connection
    pooling adds complexity without measurable benefit here.
  - messaging_type="RESPONSE" is required by Meta for replies within the
    24-hour standard messaging window. Using RESPONSE outside that window
    returns a 200 Messenger Error code 10 — handled by _raise_for_reply_error.
  - The raw Page Access Token is passed in from the Celery layer after
    Fernet decryption. This module never touches the database.
  - Transient errors are raised with an explicit retry contract; permanent
    Graph errors fail immediately and are captured by the worker audit trail.
"""

import logging

import httpx

from app.services.meta import GRAPH_BASE
from app.core.observability import observe_operation

logger = logging.getLogger(__name__)

_TRANSIENT_META_CODES = {1, 2, 4, 17, 32, 341, 613}


class GraphAPIError(RuntimeError):
    """Sanitized Meta reply error with an explicit retry contract."""

    def __init__(self, *, retryable: bool, code: int | None = None) -> None:
        self.retryable = retryable
        self.code = code
        super().__init__("Meta Graph API reply failed.")


def _raise_for_reply_error(response: httpx.Response) -> None:
    try:
        data = response.json()
    except ValueError:
        response.raise_for_status()
        return

    error = data.get("error")
    if error:
        code = error.get("code")
        retryable = bool(error.get("is_transient")) or code in _TRANSIENT_META_CODES
        retryable = retryable or response.status_code == 429 or response.status_code >= 500
        raise GraphAPIError(retryable=retryable, code=code)
    response.raise_for_status()


async def send_messenger_reply(
    page_access_token: str,
    recipient_psid: str,
    message_text: str,
) -> bool:
    """
    Send a private message reply to a Messenger user (PSID).

    Meta endpoint: POST /v20.0/me/messages
    messaging_type RESPONSE: allowed within the 24-hour customer service window.

    Args:
        page_access_token: Decrypted Page Access Token for the Facebook Page.
        recipient_psid:    Page-Scoped User ID of the customer (sender).
        message_text:      The AI-generated reply to send.

    Returns:
        True on success. Raises GraphAPIError or an httpx exception on failure.
    """
    if not message_text or not message_text.strip():
        raise GraphAPIError(retryable=False)

    payload = {
        "messaging_type": "RESPONSE",
        "recipient": {"id": recipient_psid},
        "message": {"text": message_text[:2000]},  # Meta hard limit: 2 000 chars
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        with observe_operation("meta", "messenger_reply"):
            response = await client.post(
                f"{GRAPH_BASE}/me/messages",
                params={"access_token": page_access_token},
                json=payload,
            )
            _raise_for_reply_error(response)

    data = response.json()
    message_id = data.get("message_id", "unknown")
    logger.info(
        "Messenger reply sent → recipient=%s message_id=%s",
        recipient_psid, message_id,
    )
    return True


async def post_comment_reply(
    page_access_token: str,
    comment_id: str,
    message_text: str,
) -> bool:
    """
    Post a public reply to a comment on a Facebook Page post.

    Meta endpoint: POST /v20.0/{comment_id}/comments

    Unlike Messenger, Page comment replies have NO 24-hour window restriction.
    The reply appears as a threaded response under the original comment.

    Args:
        page_access_token: Decrypted Page Access Token for the Facebook Page.
        comment_id:        Graph API ID of the comment to reply to.
        message_text:      The AI-generated reply to post.

    Returns:
        True on success. Raises GraphAPIError or an httpx exception on failure.
    """
    if not message_text or not message_text.strip():
        raise GraphAPIError(retryable=False)

    async with httpx.AsyncClient(timeout=15.0) as client:
        with observe_operation("meta", "comment_reply"):
            response = await client.post(
                f"{GRAPH_BASE}/{comment_id}/comments",
                params={"access_token": page_access_token},
                json={"message": message_text[:8000]},
            )
            _raise_for_reply_error(response)

    data = response.json()
    new_comment_id = data.get("id", "unknown")
    logger.info(
        "Comment reply posted → parent_comment=%s new_comment=%s",
        comment_id, new_comment_id,
    )
    return True
