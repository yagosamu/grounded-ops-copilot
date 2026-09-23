"""Publish only durable ingestion identities to Celery."""

from celery import Celery


class CeleryIngestionQueue:
    def __init__(self, app: Celery) -> None:
        self.app = app

    def publish(self, tenant_id: str, job_id: str) -> None:
        self.app.send_task(
            "grounded_ops.process_ingestion",
            args=(tenant_id, job_id),
            serializer="json",
            retry=False,
        )
