"""Durable Celery delivery and recovery around the real ingestion repository."""

import json
import os
import subprocess
from datetime import UTC, datetime
from time import monotonic, sleep

import pytest
from celery.contrib.testing.worker import start_worker
from mypy_boto3_s3 import S3Client
from opensearchpy import OpenSearch
from sqlalchemy import Engine, text

from adapters.object_store.document_store import DocumentStore
from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.postgres.ingestion_repository import IngestionRepository
from adapters.sources.markdown import SourceEvent
from domain.ingestion import JobState, Source
from grounded_ops import worker
from modules.ingestion.pipeline import IndexPreparationError, PreparedChunk

pytestmark = pytest.mark.integration


class RecordingSink:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.chunks: dict[str, PreparedChunk] = {}

    def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
        if self.failures:
            self.failures -= 1
            raise IndexPreparationError("index unavailable")
        self.chunks.update({chunk.id: chunk for chunk in chunks})


class RecordingQueue:
    def __init__(self) -> None:
        self.messages: list[tuple[str, str]] = []

    def publish(self, tenant_id: str, job_id: str) -> None:
        self.messages.append((tenant_id, job_id))


def test_local_compose_runs_separate_worker_and_private_broker() -> None:
    environment = os.environ | {
        "POSTGRES_PASSWORD": "test-password",
        "MINIO_ROOT_USER": "test-user",
        "MINIO_ROOT_PASSWORD": "test-password",
    }
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    )
    services = json.loads(result.stdout)["services"]
    command = services["worker"]["command"]
    assert command[:4] == ["celery", "-A", "grounded_ops.worker:celery_app", "worker"]
    assert "--beat" in command
    assert "--schedule=/tmp/celerybeat-schedule" in command
    assert services["worker"]["environment"]["REDIS_URL"] == "redis://redis:6379/0"
    assert services["worker"]["build"]["target"] == "api"
    assert services["redis"]["ports"][0]["host_ip"] == "127.0.0.1"


def _event(version: str = "v1") -> SourceEvent:
    content = f"# Runbook\n\nRecovery procedure {version}.\n".encode()
    return SourceEvent(
        "new",
        Source("service", "alpha", "markdown", "public-fixture", ("engineers",)),
        "runbooks/service.md",
        version,
        datetime(2026, 9, 23, tzinfo=UTC),
        content,
        "CC-BY-4.0",
    )


def _wait_for_state(database: Engine, job_id: str, expected: JobState) -> None:
    deadline = monotonic() + 30
    while monotonic() < deadline:
        with database.begin() as connection:
            job = IngestionRepository(connection).get_job("alpha", job_id)
        if job is not None and job.state == expected:
            return
        sleep(0.1)
    pytest.fail(f"ingestion job did not reach {expected.value}")


def test_real_queue_delivers_once_and_duplicate_is_idempotent(
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    sink = RecordingSink()
    worker.celery_app.conf.update(
        broker_url=redis_url,
        result_backend=redis_url,
        task_always_eager=False,
        task_ignore_result=False,
    )
    monkeypatch.setattr(
        worker,
        "_build_runtime",
        lambda: worker.WorkerRuntime(database, store, lambda _repository: sink),
    )
    queue = worker.CeleryIngestionQueue(worker.celery_app)
    with start_worker(
        worker.celery_app, perform_ping_check=False, pool="solo", concurrency=1
    ):
        first = worker.submit_event(_event(), database, store, queue)
        second = worker.submit_event(_event(), database, store, queue)
        assert first.id == second.id
        _wait_for_state(database, first.id, JobState.COMPLETED)

    with database.begin() as connection:
        repository = IngestionRepository(connection)
        job = repository.get_job("alpha", first.id)
        document = repository.get_document_by_key(
            "alpha", "service", "runbooks/service.md"
        )
        assert document is not None
        versions = repository.list_versions("alpha", document.id)
    assert job is not None and job.state == JobState.COMPLETED
    assert len(versions) == 1
    assert document.current_version_id == versions[0].id
    assert len(sink.chunks) > 0


def test_retry_resumes_from_persisted_checkpoint(
    database: Engine, s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    sink = RecordingSink(failures=1)
    queue = RecordingQueue()
    job = worker.submit_event(_event(), database, store, queue)

    first = worker.run_job("alpha", job.id, database, store, lambda _repo: sink)
    assert first == JobState.RETRYING.value
    with database.begin() as connection:
        checkpoint = IngestionRepository(connection).get_job("alpha", job.id)
    assert checkpoint is not None
    assert checkpoint.resume_from == JobState.INDEXING
    assert checkpoint.error_class == "index_preparation"
    assert checkpoint.attempts == 1

    second = worker.run_job("alpha", job.id, database, store, lambda _repo: sink)
    assert second == JobState.COMPLETED.value
    with database.begin() as connection:
        final = IngestionRepository(connection).get_job("alpha", job.id)
    assert final is not None and final.state == JobState.COMPLETED
    assert len(sink.chunks) > 0


def test_worker_uses_the_measured_structural_chunk_bound(
    database: Engine, s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    original = _event()
    event = SourceEvent(
        original.kind,
        original.source,
        original.canonical_key,
        original.source_version,
        original.source_timestamp,
        ("# Runbook\n\n" + " ".join(f"step{index}" for index in range(200))).encode(),
        original.license,
    )
    sink = RecordingSink()
    job = worker.submit_event(event, database, store, RecordingQueue())

    assert (
        worker.run_job("alpha", job.id, database, store, lambda _repo: sink)
        == JobState.COMPLETED.value
    )
    assert len(sink.chunks) > 1
    assert all(
        len(chunk.text.split()) <= worker.CHUNK_MAX_TOKENS
        for chunk in sink.chunks.values()
    )
    assert worker.CHUNK_MAX_TOKENS == 80


def test_executable_worker_projects_real_authorized_index(
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
    opensearch_client: OpenSearch,
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    job = worker.submit_event(_event(), database, store, RecordingQueue())

    assert (
        worker.run_job(
            "alpha",
            job.id,
            database,
            store,
            lambda repository: worker.OpenSearchPreparationSink(
                repository, opensearch_client
            ),
        )
        == JobState.COMPLETED.value
    )
    with database.begin() as connection:
        repository = IngestionRepository(connection)
        document = repository.get_document_by_key(
            "alpha", "service", "runbooks/service.md"
        )
        assert document is not None
        version_id = document.current_version_id
    assert version_id is not None
    result = opensearch_client.search(
        index=LexicalIndexSchema(opensearch_client).read_alias,
        body={
            "query": {
                "bool": {
                    "filter": [
                        {"term": {"document_version_id": version_id}},
                        {"term": {"is_current": True}},
                    ]
                }
            }
        },
    )
    hits = result["hits"]["hits"]
    assert len(hits) > 0
    assert all(hit["_source"]["tenant_id"] == "alpha" for hit in hits)
    assert all(hit["_source"]["policy"] == ["engineers"] for hit in hits)


def test_interrupted_delivery_is_recovered_from_postgres(
    database: Engine, s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    queue = RecordingQueue()
    job = worker.submit_event(_event(), database, store, queue)

    class InterruptedSink:
        def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        worker.run_job(
            "alpha", job.id, database, store, lambda _repo: InterruptedSink()
        )
    with database.begin() as connection:
        persisted = IngestionRepository(connection).get_job("alpha", job.id)
        delivery_attempts = connection.execute(
            text("SELECT delivery_attempts FROM ingestion_jobs WHERE id=:id"),
            {"id": job.id},
        ).scalar_one()
        connection.execute(
            text(
                "UPDATE ingestion_jobs SET dispatched_at=now()-interval '2 minutes' "
                "WHERE id=:id"
            ),
            {"id": job.id},
        )
    assert persisted is not None and persisted.state == JobState.QUEUED
    assert delivery_attempts == 1

    assert worker.recover_pending(database, queue) == 1
    assert queue.messages[-1] == ("alpha", job.id)
    assert (
        worker.run_job("alpha", job.id, database, store, lambda _repo: RecordingSink())
        == JobState.COMPLETED.value
    )


def test_broker_publish_failure_keeps_a_recoverable_job(
    database: Engine, s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)

    class UnavailableQueue:
        def publish(self, tenant_id: str, job_id: str) -> None:
            raise ConnectionError("broker unavailable")

    with pytest.raises(ConnectionError):
        worker.submit_event(_event(), database, store, UnavailableQueue())
    with database.begin() as connection:
        row = (
            connection.execute(text("SELECT tenant_id,id,state FROM ingestion_jobs"))
            .mappings()
            .one()
        )
    assert row["tenant_id"] == "alpha"
    assert row["state"] == JobState.QUEUED.value

    queue = RecordingQueue()
    assert worker.recover_pending(database, queue) == 1
    assert queue.messages == [("alpha", str(row["id"]))]
    assert (
        worker.run_job(
            "alpha", str(row["id"]), database, store, lambda _repo: RecordingSink()
        )
        == JobState.COMPLETED.value
    )


def test_unexpected_worker_error_is_redacted_and_resumable(
    database: Engine, s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    job = worker.submit_event(_event(), database, store, RecordingQueue())

    class BrokenSink:
        def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
            raise RuntimeError("sensitive document content")

    assert (
        worker.run_job("alpha", job.id, database, store, lambda _repo: BrokenSink())
        == JobState.RETRYING.value
    )
    with database.begin() as connection:
        checkpoint = IngestionRepository(connection).get_job("alpha", job.id)
    assert checkpoint is not None
    assert checkpoint.error_class == "worker_internal"
    assert checkpoint.attempts == 1
    assert checkpoint.resume_from == JobState.FETCHING
    assert (
        worker.run_job("alpha", job.id, database, store, lambda _repo: RecordingSink())
        == JobState.COMPLETED.value
    )


def test_repeated_worker_loss_ends_in_redacted_failure(
    database: Engine, s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    job = worker.submit_event(_event(), database, store, RecordingQueue())

    class InterruptedSink:
        def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
            raise KeyboardInterrupt

    for _ in range(3):
        with pytest.raises(KeyboardInterrupt):
            worker.run_job(
                "alpha", job.id, database, store, lambda _repo: InterruptedSink()
            )
    assert (
        worker.run_job("alpha", job.id, database, store, lambda _repo: RecordingSink())
        == JobState.FAILED.value
    )
    with database.begin() as connection:
        final = IngestionRepository(connection).get_job("alpha", job.id)
    assert final is not None
    assert final.state == JobState.FAILED
    assert final.error_class == "worker_lost"


def test_worker_shutdown_leaves_committed_result(
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
    redis_url: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    worker.celery_app.conf.update(
        broker_url=redis_url,
        result_backend=redis_url,
        task_always_eager=False,
        task_ignore_result=False,
    )
    monkeypatch.setattr(
        worker,
        "_build_runtime",
        lambda: worker.WorkerRuntime(
            database, store, lambda _repository: RecordingSink()
        ),
    )
    with start_worker(
        worker.celery_app, perform_ping_check=False, pool="solo", concurrency=1
    ):
        job = worker.submit_event(
            _event(), database, store, worker.CeleryIngestionQueue(worker.celery_app)
        )
        _wait_for_state(database, job.id, JobState.COMPLETED)
    with database.begin() as connection:
        final = IngestionRepository(connection).get_job("alpha", job.id)
    assert final is not None and final.state == JobState.COMPLETED
