"""Executable Celery ingestion worker with PostgreSQL-backed recovery."""

import argparse
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import boto3
from botocore.config import Config
from celery import Celery
from opensearchpy import OpenSearch
from sqlalchemy import URL, Engine, create_engine, text

from adapters.object_store.document_store import ArtifactRef, DocumentStore
from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.opensearch.index_writer import (
    IndexChunk,
    OpenSearchIndexWriter,
    VersionProjection,
)
from adapters.postgres.ingestion_repository import IngestionRepository
from adapters.postgres.migrations import migrate
from adapters.queue.celery_queue import CeleryIngestionQueue
from adapters.sources.markdown import MarkdownCorpus, SourceEvent
from domain.ingestion import IngestionJob, JobState
from modules.chunking.structural import StructuralChunker
from modules.ingestion.pipeline import (
    IndexPreparationError,
    IndexPreparationSink,
    IngestionPipeline,
    PipelineInputError,
    PreparedChunk,
)
from modules.parsing.parser import MarkdownParser
from modules.policy.enforcement import PolicyEnforcer
from observability.telemetry import EntryPoint, Telemetry

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

MAX_DELIVERIES = 3
CHUNK_MAX_TOKENS = 80

celery_app = Celery(
    "grounded_ops",
    broker=os.getenv("REDIS_URL", "redis://redis:6379/0"),
)
celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,
    task_time_limit=120,
    broker_transport_options={"visibility_timeout": 180},
    beat_schedule={
        "recover-ingestion": {
            "task": "grounded_ops.recover_ingestion",
            "schedule": 30.0,
        }
    },
)


class IngestionQueue(Protocol):
    def publish(self, tenant_id: str, job_id: str) -> None: ...


@dataclass(frozen=True)
class WorkerRuntime:
    engine: Engine
    store: DocumentStore
    sink_factory: Callable[[IngestionRepository], IndexPreparationSink]
    close: Callable[[], None] | None = None


class OpenSearchPreparationSink:
    def __init__(self, repository: IngestionRepository, client: OpenSearch) -> None:
        self.repository = repository
        self.client = client

    def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
        if not chunks:
            return
        first = chunks[0]
        source = self.repository.get_source(first.tenant_id, first.source_id)
        version = self.repository.get_version(
            first.tenant_id, first.document_version_id
        )
        if source is None or version is None:
            raise IndexPreparationError("projection metadata unavailable")
        projection = VersionProjection(
            first.tenant_id,
            first.source_id,
            first.document_id,
            first.document_version_id,
            source.policy,
            version.source_timestamp,
            tuple(
                IndexChunk(
                    chunk.id,
                    chunk.ordinal,
                    chunk.text,
                    chunk.start,
                    chunk.end,
                    chunk.content_hash,
                    chunk.parser_version,
                    chunk.chunker_version,
                )
                for chunk in chunks
            ),
        )
        try:
            schema = LexicalIndexSchema(self.client)
            schema.ensure("1")
            OpenSearchIndexWriter(
                self.client, schema.write_alias, PolicyEnforcer(self.repository)
            ).upsert(projection)
        except Exception:
            raise IndexPreparationError("index preparation unavailable") from None


def submit_event(
    event: SourceEvent,
    engine: Engine,
    store: DocumentStore,
    queue: IngestionQueue,
) -> IngestionJob:
    if event.kind not in {"new", "changed", "unchanged"} or event.content is None:
        raise PipelineInputError("invalid source event")
    content = event.content
    store.put(event.source.tenant_id, "raw", content)
    with engine.begin() as connection:
        repository = IngestionRepository(connection)
        submission = repository.submit(
            event.source,
            event.canonical_key,
            event.source_version,
            sha256(content).hexdigest(),
            event.source_timestamp,
            MarkdownParser.VERSION,
        )
    if submission.job.state not in {JobState.COMPLETED, JobState.FAILED}:
        queue.publish(event.source.tenant_id, submission.job.id)
        with engine.begin() as connection:
            IngestionRepository(connection).mark_dispatched(
                event.source.tenant_id, submission.job.id
            )
    return submission.job


def recover_pending(engine: Engine, queue: IngestionQueue, limit: int = 100) -> int:
    with engine.begin() as connection:
        pending = IngestionRepository(connection).claim_recovery_batch(limit)
    for tenant_id, job_id in pending:
        queue.publish(tenant_id, job_id)
    return len(pending)


def run_job(
    tenant_id: str,
    job_id: str,
    engine: Engine,
    store: DocumentStore,
    sink_factory: Callable[[IngestionRepository], IndexPreparationSink],
) -> str:
    lock_key = int.from_bytes(
        sha256(f"{tenant_id}:{job_id}".encode()).digest()[:8], signed=True
    )
    with engine.connect() as connection:
        acquired = bool(
            connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}
            ).scalar_one()
        )
        connection.commit()
        if not acquired:
            return "busy"
        try:
            with connection.begin():
                repository = IngestionRepository(connection)
                deliveries = repository.claim_delivery(tenant_id, job_id)
                if deliveries is None:
                    job = repository.get_job(tenant_id, job_id)
                    if job is None:
                        raise PipelineInputError("unknown ingestion job")
                    return job.state.value
                if deliveries > MAX_DELIVERIES:
                    repository.fail_lost_delivery(tenant_id, job_id)
                    return JobState.FAILED.value
            try:
                with connection.begin():
                    repository = IngestionRepository(connection)
                    delivery = repository.get_delivery(tenant_id, job_id)
                    if delivery is None:
                        raise PipelineInputError("unknown ingestion job")
                    content = store.get(
                        tenant_id,
                        ArtifactRef(tenant_id, "raw", delivery.content_hash),
                    )
                    event = SourceEvent(
                        "new",
                        delivery.source,
                        delivery.canonical_key,
                        delivery.source_version,
                        delivery.source_timestamp,
                        content,
                        "unspecified",
                    )
                    pipeline = IngestionPipeline(
                        repository,
                        store,
                        MarkdownParser(),
                        StructuralChunker(max_tokens=CHUNK_MAX_TOKENS),
                        sink_factory(repository),
                        max_attempts=MAX_DELIVERIES,
                    )
                    outcome = pipeline.resume(tenant_id, job_id, event)
                return outcome.kind
            except Exception:
                with connection.begin():
                    repository = IngestionRepository(connection)
                    repository.record_worker_error(
                        tenant_id, job_id, max_attempts=MAX_DELIVERIES
                    )
                    job = repository.get_job(tenant_id, job_id)
                if job is None:
                    raise PipelineInputError("unknown ingestion job") from None
                return job.state.value
        finally:
            connection.execute(
                text("SELECT pg_advisory_unlock(:key)"), {"key": lock_key}
            )
            connection.commit()


def _build_runtime() -> WorkerRuntime:
    password = os.environ["POSTGRES_PASSWORD"]
    database_url = URL.create(
        "postgresql+psycopg",
        username="grounded_ops",
        password=password,
        host=os.getenv("POSTGRES_HOST", "postgres"),
        database="grounded_ops",
    )
    engine = create_engine(database_url, pool_pre_ping=True)
    s3: S3Client = boto3.client(
        "s3",
        endpoint_url=os.environ["S3_ENDPOINT"],
        aws_access_key_id=os.environ["MINIO_ROOT_USER"],
        aws_secret_access_key=os.environ["MINIO_ROOT_PASSWORD"],
        region_name="us-east-1",
        config=Config(
            connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1}
        ),
    )
    store = DocumentStore(s3, os.environ["S3_BUCKET"])
    client = OpenSearch(
        hosts=[os.environ["OPENSEARCH_URL"]],
        timeout=5,
        max_retries=1,
    )

    def close() -> None:
        client.close()
        s3.close()
        engine.dispose()

    return WorkerRuntime(
        engine,
        store,
        lambda repository: OpenSearchPreparationSink(repository, client),
        close,
    )


@celery_app.task(name="grounded_ops.process_ingestion")
def process_ingestion(tenant_id: str, job_id: str) -> str:
    runtime = _build_runtime()
    try:
        telemetry = Telemetry()
        with telemetry.bind(
            telemetry.new_context(job_id, entry_point=EntryPoint.WORKER)
        ):
            result = run_job(
                tenant_id,
                job_id,
                runtime.engine,
                runtime.store,
                runtime.sink_factory,
            )
        if result == JobState.RETRYING.value:
            process_ingestion.apply_async(args=(tenant_id, job_id), countdown=5)
        return result
    finally:
        if runtime.close is not None:
            runtime.close()


@celery_app.task(name="grounded_ops.recover_ingestion")
def recover_ingestion() -> int:
    runtime = _build_runtime()
    try:
        return recover_pending(runtime.engine, CeleryIngestionQueue(celery_app))
    finally:
        if runtime.close is not None:
            runtime.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Submit or recover ingestion jobs")
    actions = parser.add_subparsers(dest="action", required=True)
    submit = actions.add_parser("submit-corpus")
    submit.add_argument("root", type=Path)
    submit.add_argument("--tenant", required=True)
    submit.add_argument("--policy", required=True)
    actions.add_parser("recover")
    actions.add_parser("migrate")
    arguments = parser.parse_args()
    runtime = _build_runtime()
    try:
        queue = CeleryIngestionQueue(celery_app)
        if arguments.action == "migrate":
            with runtime.engine.begin() as connection:
                migrate(connection, "head")
        elif arguments.action == "recover":
            print(recover_pending(runtime.engine, queue))
        else:
            corpus = MarkdownCorpus(
                arguments.root,
                arguments.tenant,
                tuple(arguments.policy.split(",")),
            )
            for event in corpus.scan(datetime.now(UTC)):
                if event.kind == "malformed":
                    raise PipelineInputError("invalid corpus source")
                job = submit_event(event, runtime.engine, runtime.store, queue)
                print(job.id)
    finally:
        if runtime.close is not None:
            runtime.close()


if __name__ == "__main__":
    main()
