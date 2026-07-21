

import json
import logging
import os
import sys
from datetime import datetime, timezone
from functools import lru_cache

from app.core.config import settings
from app.core.observability import current_request_id, current_task_id

_EXTRA_FIELDS = (
    "event",
    "method",
    "route",
    "status_code",
    "duration_ms",
    "component",
    "operation",
    "outcome",
)


class JsonFormatter(logging.Formatter):


    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "environment": settings.ENVIRONMENT,
        }
        request_id = current_request_id()
        task_id = current_task_id()
        if request_id:
            payload["request_id"] = request_id
        if task_id:
            payload["task_id"] = task_id
        for field in _EXTRA_FIELDS:
            value = getattr(record, field, None)
            if value is not None:
                payload[field] = value
        if record.exc_info and record.exc_info[0] is not None:
            payload["exception_type"] = record.exc_info[0].__name__
            traceback = record.exc_info[2]
            while traceback and traceback.tb_next:
                traceback = traceback.tb_next
            if traceback:
                frame = traceback.tb_frame
                payload["exception_location"] = {
                    "file": os.path.basename(frame.f_code.co_filename),
                    "function": frame.f_code.co_name,
                    "line": traceback.tb_lineno,
                }
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(level)
    if settings.LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    root.addHandler(handler)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


configure_logging()


@lru_cache(maxsize=None)
def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
