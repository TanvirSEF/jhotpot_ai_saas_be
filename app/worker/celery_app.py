"""
Celery application configuration — NexusSuite (Phase A5 update)

Queue Architecture:
  ┌─────────────────────────────────────────────────────────────┐
  │  Queue       │ Priority │ Worker flag        │ Tasks        │
  ├─────────────────────────────────────────────────────────────┤
  │  webhooks    │ HIGH  (9)│ -Q webhooks        │ process_fb_  │
  │              │          │                    │ webhook      │
  ├─────────────────────────────────────────────────────────────┤
  │  default     │ MED   (5)│ -Q default         │ future misc  │
  ├─────────────────────────────────────────────────────────────┤
  │  embeddings  │ LOW   (1)│ -Q embeddings      │ generate_    │
  │              │          │                    │ embeddings   │
  └─────────────────────────────────────────────────────────────┘

Why priority matters:
  A single slow embedding task (OpenAI API ~1-2s) queued behind 50 webhook
  tasks would block customer replies for minutes. Separate queues allow
  each to scale and schedule independently.

Start workers:
  # High-priority webhook worker (at least 1)
  celery -A app.worker.celery_app worker -Q webhooks -c 4 --loglevel=info

  # Low-priority embedding worker (can be 1 per machine)
  celery -A app.worker.celery_app worker -Q embeddings -c 2 --loglevel=info

  # Or run all queues on one worker (dev only):
  celery -A app.worker.celery_app worker -Q webhooks,embeddings,default -c 4 --loglevel=info
"""

from celery import Celery
from kombu import Queue

from app.core.config import settings

celery_app = Celery(
    "nexussuite",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.worker.tasks"],
)

celery_app.conf.update(
    # ── Serialisation ─────────────────────────────────────────────────────────
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],

    # ── Timezone ──────────────────────────────────────────────────────────────
    timezone="UTC",
    enable_utc=True,

    # ── Reliability ───────────────────────────────────────────────────────────
    # acks_late: task is acknowledged AFTER completion, not on receipt.
    # If the worker dies mid-task the broker will re-queue it.
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,
    task_send_sent_event=True,
    broker_connection_retry_on_startup=True,
    broker_transport_options={"visibility_timeout": 300},

    # Result TTL: keep task results in Redis for 24h for debugging.
    result_expires=86_400,

    # ── Priority Queues ───────────────────────────────────────────────────────
    # Three queues with Redis priority levels (0=lowest, 9=highest).
    # kombu Queue priority is enforced by the broker; workers must be
    # started with explicit -Q flags to consume the right queue.
    task_queues=(
        Queue("webhooks",   routing_key="webhooks",   queue_arguments={"x-max-priority": 10}),
        Queue("default",    routing_key="default",    queue_arguments={"x-max-priority": 10}),
        Queue("embeddings", routing_key="embeddings", queue_arguments={"x-max-priority": 10}),
    ),
    task_default_queue="default",
    task_default_priority=5,

    # ── Task Routing ──────────────────────────────────────────────────────────
    # Routes tasks to the appropriate queue by task name.
    task_routes={
        "process_fb_webhook":  {"queue": "webhooks",   "priority": 9},
        "recover_fb_webhook_inbox": {"queue": "webhooks", "priority": 9},
        "generate_embeddings": {"queue": "embeddings", "priority": 1},
        "export_resume_pdf":   {"queue": "default",    "priority": 5},
    },

    # ── Beat Scheduler ────────────────────────────────────────────────────────
    beat_scheduler="celery.beat:PersistentScheduler",
    beat_schedule={
        "recover-meta-webhook-inbox": {
            "task": "recover_fb_webhook_inbox",
            "schedule": 60.0,
        },
    },

    # ── Worker concurrency hint (overridable via CLI) ─────────────────────────
    # Prefer I/O-bound gevent/eventlet for webhook tasks; process pool for CPU.
    worker_prefetch_multiplier=1,   # process one task at a time per worker slot
                                    # prevents a slow task from hogging the prefetch buffer
)
