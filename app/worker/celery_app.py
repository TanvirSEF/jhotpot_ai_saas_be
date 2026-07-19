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
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    result_expires=86400,
    beat_scheduler="celery.beat:PersistentScheduler",
)
