


from celery import Celery
from kombu import Queue

from app.core.config import settings
from app.worker import observability as _observability  # noqa: F401

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
    task_track_started=True,
    task_send_sent_event=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 300},


    result_expires=86_400,


    task_queues=(
        Queue("webhooks",   routing_key="webhooks",   queue_arguments={"x-max-priority": 10}),
        Queue("default",    routing_key="default",    queue_arguments={"x-max-priority": 10}),
        Queue("embeddings", routing_key="embeddings", queue_arguments={"x-max-priority": 10}),
    ),
    task_default_queue="default",
    task_default_priority=5,


    task_routes={
        "process_fb_webhook":  {"queue": "webhooks",   "priority": 9},
        "recover_fb_webhook_inbox": {"queue": "webhooks", "priority": 9},
        "generate_embeddings": {"queue": "embeddings", "priority": 1},
        "export_resume_pdf":   {"queue": "default",    "priority": 5},
        "recover_resume_exports": {"queue": "default", "priority": 5},
    },


    beat_scheduler="celery.beat:PersistentScheduler",
    beat_schedule={
        "recover-meta-webhook-inbox": {
            "task": "recover_fb_webhook_inbox",
            "schedule": 60.0,
        },
        "recover-stale-resume-exports": {
            "task": "recover_resume_exports",
            "schedule": 60.0,
        },
    },


    worker_prefetch_multiplier=1,

)
