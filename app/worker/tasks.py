import logging
from app.worker.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(
    name="process_fb_webhook",
    bind=True,
    max_retries=3,
    default_retry_delay=5,
)
def process_fb_webhook(self, payload: dict) -> dict:
    try:
        logger.info(f"Processing webhook payload: {payload}")
        return {"status": "queued", "payload": payload}
    except Exception as exc:
        logger.error(f"Failed to process webhook: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="generate_embeddings",
    bind=True,
    max_retries=3,
    default_retry_delay=10,
)
def generate_embeddings(self, entity_type: str, entity_id: str) -> dict:
    try:
        logger.info(f"Generating embeddings for {entity_type}:{entity_id}")
        return {"status": "queued", "entity_type": entity_type, "entity_id": entity_id}
    except Exception as exc:
        logger.error(f"Failed to generate embedding: {exc}", exc_info=True)
        raise self.retry(exc=exc)


@celery_app.task(
    name="export_resume_pdf",
    bind=True,
    max_retries=2,
    default_retry_delay=15,
)
def export_resume_pdf(self, resume_id: str) -> dict:
    try:
        logger.info(f"Exporting PDF for resume: {resume_id}")
        return {"status": "queued", "resume_id": resume_id}
    except Exception as exc:
        logger.error(f"Failed to export resume PDF: {exc}", exc_info=True)
        raise self.retry(exc=exc)
