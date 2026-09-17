"""ING-01/02 recoverable orchestration through the public pipeline seam."""

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from adapters.postgres.ingestion_repository import Submission
from adapters.sources.markdown import SourceEvent
from domain.ingestion import Document, DocumentVersion, IngestionJob, JobState, Source
from modules.chunking.structural import StructuralChunker
from modules.ingestion.pipeline import (
    IndexPreparationError,
    IngestionPipeline,
    PreparedChunk,
)
from modules.parsing.parser import MarkdownParser

pytestmark = pytest.mark.unit
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class StoredRef:
    key: str


class MemoryStore:
    def __init__(self) -> None:
        self.puts: list[tuple[str, str, bytes]] = []

    def put(self, tenant_id: str, kind: str, content: bytes) -> StoredRef:
        self.puts.append((tenant_id, kind, content))
        return StoredRef(f"{tenant_id}/{kind}/{sha256(content).hexdigest()}")


class MemoryRepository:
    def __init__(self) -> None:
        self.documents: dict[tuple[str, str, str], Document] = {}
        self.versions: dict[tuple[str, str, str], DocumentVersion] = {}
        self.jobs: dict[str, IngestionJob] = {}

    def submit(
        self,
        source: Source,
        canonical_key: str,
        source_version: str,
        content_hash: str,
        source_timestamp: datetime,
        parser_version: str,
    ) -> Submission:
        document_key = (source.tenant_id, source.id, canonical_key)
        document = self.documents.setdefault(
            document_key,
            Document(
                f"d{len(self.documents) + 1}",
                source.tenant_id,
                source.id,
                canonical_key,
            ),
        )
        version_key = (source.tenant_id, document.id, source_version)
        version = self.versions.get(version_key)
        if version is None:
            version = DocumentVersion(
                f"v{len(self.versions) + 1}",
                source.tenant_id,
                document.id,
                source_version,
                content_hash,
                source_timestamp,
                parser_version,
            )
            self.versions[version_key] = version
            job = IngestionJob(
                f"j{len(self.jobs) + 1}",
                source.tenant_id,
                f"retry{len(self.jobs) + 1}",
            )
            self.jobs[job.id] = job
        job = next(
            item
            for item in self.jobs.values()
            if item.tenant_id == source.tenant_id
            and item.idempotency_key.endswith(version.id.removeprefix("v"))
        )
        return Submission(document, version, job)

    def get_job(self, tenant_id: str, job_id: str) -> IngestionJob | None:
        job = self.jobs.get(job_id)
        return job if job is not None and job.tenant_id == tenant_id else None

    def save_job(self, job: IngestionJob) -> IngestionJob:
        self.jobs[job.id] = job
        return job

    def update_artifacts(
        self, tenant_id: str, version_id: str, raw_ref: str, normalized_ref: str
    ) -> DocumentVersion:
        key, version = next(
            (key, item)
            for key, item in self.versions.items()
            if item.tenant_id == tenant_id and item.id == version_id
        )
        updated = replace(version, raw_ref=raw_ref, normalized_ref=normalized_ref)
        self.versions[key] = updated
        return updated

    def promote(self, tenant_id: str, version_id: str) -> None:
        version = next(
            item
            for item in self.versions.values()
            if item.tenant_id == tenant_id and item.id == version_id
        )
        key, document = next(
            (key, item)
            for key, item in self.documents.items()
            if item.tenant_id == tenant_id and item.id == version.document_id
        )
        self.documents[key] = replace(document, current_version_id=version_id)

    def get_document_by_key(
        self, tenant_id: str, source_id: str, canonical_key: str
    ) -> Document | None:
        return self.documents.get((tenant_id, source_id, canonical_key))

    def delete(self, tenant_id: str, document_id: str) -> None:
        key, document = next(
            (key, item)
            for key, item in self.documents.items()
            if item.tenant_id == tenant_id and item.id == document_id
        )
        self.documents[key] = document.delete()


class RecordingSink:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.calls = 0
        self.records: dict[str, PreparedChunk] = {}

    def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
        self.calls += 1
        self.records.update({chunk.id: chunk for chunk in chunks})
        if self.calls <= self.failures:
            raise IndexPreparationError("private provider detail")


def event(
    content: bytes = b"# Guide\none two three\n", kind: str = "new"
) -> SourceEvent:
    return SourceEvent(
        kind,
        Source("otel", "alpha", "markdown", "https://example.test", ("read",)),
        "guide.md",
        sha256(content).hexdigest(),
        STAMP,
        content if kind != "deleted" else None,
        "Apache-2.0",
    )


def pipeline(
    repository: MemoryRepository, store: MemoryStore, sink: RecordingSink, retries: int
) -> IngestionPipeline:
    return IngestionPipeline(
        repository,
        store,
        MarkdownParser(),
        StructuralChunker(max_tokens=4),
        sink,
        max_attempts=retries,
    )


def test_partial_index_failure_resumes_idempotently_and_repeat_is_a_noop() -> None:
    repository, store, sink = MemoryRepository(), MemoryStore(), RecordingSink(1)
    subject = pipeline(repository, store, sink, retries=3)

    partial = subject.run(event())
    completed = subject.resume("alpha", partial.job.id, event())
    repeated = subject.run(event())

    assert partial.job.state == JobState.RETRYING
    assert partial.job.resume_from == JobState.INDEXING
    assert partial.job.attempts == 1
    assert partial.job.error_class == "index_preparation"
    assert completed.job.state == JobState.COMPLETED
    assert completed.job.id == partial.job.id == repeated.job.id
    assert len(repository.versions) == 1
    assert sink.calls == 2
    assert list(sink.records) == [chunk.id for chunk in completed.prepared_chunks]
    assert all(chunk.tenant_id == "alpha" for chunk in completed.prepared_chunks)
    assert all(
        chunk.document_version_id == completed.version_id
        for chunk in completed.prepared_chunks
    )
    assert all(
        chunk.parser_version == "markdown-1" for chunk in completed.prepared_chunks
    )
    assert all(
        chunk.chunker_version == "structural-1" for chunk in completed.prepared_chunks
    )
    assert len(store.puts) == 2


def test_retry_exhaustion_emits_redacted_dlq_and_stays_terminal() -> None:
    repository, store, sink = MemoryRepository(), MemoryStore(), RecordingSink(99)
    subject = pipeline(repository, store, sink, retries=2)

    retrying = subject.run(event())
    failed = subject.resume("alpha", retrying.job.id, event())
    terminal = subject.resume("alpha", retrying.job.id, event())

    assert retrying.job.state == JobState.RETRYING
    assert failed.job.state == terminal.job.state == JobState.FAILED
    assert failed.job.attempts == 2
    assert failed.dlq is not None
    assert failed.dlq.job_id == failed.job.id
    assert failed.dlq.retry_key == failed.job.idempotency_key
    assert failed.dlq.error_class == "index_preparation"
    assert failed.dlq.attempts == 2
    assert "private" not in repr(failed.dlq)
    assert sink.calls == 2


def test_parse_failure_retries_from_parsing_then_fails_safely() -> None:
    repository, store, sink = MemoryRepository(), MemoryStore(), RecordingSink()
    subject = pipeline(repository, store, sink, retries=2)
    malformed = event(b"\xff")

    retrying = subject.run(malformed)
    failed = subject.resume("alpha", retrying.job.id, malformed)

    assert retrying.job.resume_from == JobState.PARSING
    assert failed.job.state == JobState.FAILED
    assert failed.job.error_class == "parser_input"
    assert failed.dlq is not None
    assert failed.dlq.error_class == "parser_input"
    assert sink.records == {}


def test_deleted_event_tombstones_the_known_document_without_a_new_version() -> None:
    repository, store, sink = MemoryRepository(), MemoryStore(), RecordingSink()
    subject = pipeline(repository, store, sink, retries=2)
    completed = subject.run(event())

    deleted = subject.run(event(kind="deleted"))

    document = repository.get_document_by_key("alpha", "otel", "guide.md")
    assert completed.job.state == JobState.COMPLETED
    assert deleted.kind == "deleted"
    assert deleted.job is None
    assert document is not None
    assert document.deleted is True
    assert document.current_version_id is None
    assert len(repository.versions) == 1
