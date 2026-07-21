

import logging
import re
import time
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.observability import (
    HTTP_DURATION,
    HTTP_IN_PROGRESS,
    HTTP_REQUESTS,
    bind_request_id,
    reset_request_id,
)

HEADER_NAME = "X-Request-ID"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
logger = logging.getLogger(__name__)


def normalize_request_id(value: str | None) -> str:
    if value and _SAFE_REQUEST_ID.fullmatch(value):
        return value
    return str(uuid.uuid4())


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    return str(path) if path else "unmatched"


class RequestIDMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = normalize_request_id(request.headers.get(HEADER_NAME))
        request.state.request_id = request_id
        token = bind_request_id(request_id)
        method = request.method.upper()
        started = time.perf_counter()
        status_code = 500
        HTTP_IN_PROGRESS.labels(method).inc()
        try:
            response: Response = await call_next(request)
            status_code = response.status_code
            response.headers[HEADER_NAME] = request_id
            return response
        except Exception:
            logger.exception(
                "Unhandled HTTP exception",
                extra={"event": "http_unhandled_exception"},
            )
            raise
        finally:
            route = _route_template(request)
            duration = max(0.0, time.perf_counter() - started)
            HTTP_IN_PROGRESS.labels(method).dec()
            HTTP_REQUESTS.labels(method, route, str(status_code)).inc()
            HTTP_DURATION.labels(method, route).observe(duration)
            logger.info(
                "HTTP request completed",
                extra={
                    "event": "http_request_completed",
                    "method": method,
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": round(duration * 1000, 3),
                    "outcome": "success" if status_code < 500 else "error",
                },
            )
            reset_request_id(token)
