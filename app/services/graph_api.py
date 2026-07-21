


import logging

import httpx

from app.core.observability import observe_operation
from app.services.meta import GRAPH_BASE

logger = logging.getLogger(__name__)

_TRANSIENT_META_CODES = {1, 2, 4, 17, 32, 341, 613}


class GraphAPIError(RuntimeError):


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


    if not message_text or not message_text.strip():
        raise GraphAPIError(retryable=False)

    payload = {
        "messaging_type": "RESPONSE",
        "recipient": {"id": recipient_psid},
        "message": {"text": message_text[:2000]},
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
