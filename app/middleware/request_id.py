"""
Request Correlation ID Middleware — Phase A5

Assigns a unique UUID to every HTTP request and echoes it in the
response headers. This enables end-to-end tracing across:

  Frontend (X-Request-ID in fetch headers)
    → FastAPI access log
    → Celery task log (pass request_id in the task payload)
    → External monitoring (Sentry, Datadog, etc.)

Behaviour:
  - If the incoming request already carries X-Request-ID (e.g. from a
    load balancer or API gateway), that value is reused — preserving the
    original trace chain.
  - If absent, a new UUID4 is generated.
  - The ID is stored in request.state.request_id for use by endpoint
    handlers or custom log formatters.
  - The ID is always included in the X-Request-ID response header.

Usage (registered in main.py):
    app.add_middleware(RequestIDMiddleware)
"""

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

HEADER_NAME = "X-Request-ID"


class RequestIDMiddleware(BaseHTTPMiddleware):
    """
    Starlette middleware that injects a per-request correlation ID.
    Subclasses BaseHTTPMiddleware for clean FastAPI integration.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # Honour existing request ID from upstream (load balancer / gateway)
        request_id = request.headers.get(HEADER_NAME) or str(uuid.uuid4())

        # Attach to request state so endpoint handlers can read it
        request.state.request_id = request_id

        # Process the request
        response: Response = await call_next(request)

        # Echo ID in response so clients/proxies can correlate logs
        response.headers[HEADER_NAME] = request_id

        return response
