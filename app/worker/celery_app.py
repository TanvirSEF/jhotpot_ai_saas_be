"""
Celery application instance.

The worker is started separately from the FastAPI process:
    celery -A app.worker.celery_app worker --loglevel=info --concurrency=4

For production (Windows-compatible):
    celery -A app.worker.celery_app worker --loglevel=info --pool=solo
"""

from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "nexussuite",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    # Retry failed tasks after 5 minutes, up to 3 times by default
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    # Result expiry: 24 hours
    result_expires=86400,
    # Beat scheduler (for future periodic tasks)
    beat_scheduler="celery.beat:PersistentScheduler",
)
