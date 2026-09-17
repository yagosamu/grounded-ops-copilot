"""Phase 1 E2E ingestion with real source, PostgreSQL and object-store adapters."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from mypy_boto3_s3 import S3Client
from sqlalchemy import Engine

from adapters.object_store.document_store import DocumentStore
from adapters.postgres.ingestion_repository import IngestionRepository
from adapters.sources.markdown import MarkdownCorpus, SourceEvent
from domain.ingestion import JobState
from modules.chunking.structural import StructuralChunker
from modules.ingestion.pipeline import IngestionPipeline, PreparedChunk
from modules.parsing.parser import MarkdownParser

pytestmark = pytest.mark.integration
FIRST = datetime(2026, 1, 1, tzinfo=UTC)
SECOND = datetime(2026, 2, 1, tzinfo=UTC)


class RecordingPreparationSink:
    def __init__(self) -> None:
        self.records: dict[str, PreparedChunk] = {}

    def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
        self.records.update({chunk.id: chunk for chunk in chunks})


def select(events: list[SourceEvent], source_id: str) -> SourceEvent:
    return next(event for event in events if event.source.id == source_id)


def test_real_adapters_ingest_repeat_update_and_delete_with_full_provenance(
    tmp_path: Path,
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
) -> None:
    root = tmp_path / "corpus"
    shutil.copytree("evals/datasets/v1", root)
    corpus = MarkdownCorpus(root, "alpha", ("engineers",))
    initial_events = corpus.scan(FIRST)
    initial = select(initial_events, "otel-tracing-api")
    previous = {event.source.id: event for event in initial_events}
    sink = RecordingPreparationSink()

    with database.begin() as connection:
        repository = IngestionRepository(connection)
        pipeline = IngestionPipeline(
            repository,
            DocumentStore(s3_client, artifact_bucket),
            MarkdownParser(),
            StructuralChunker(max_tokens=40),
            sink,
            max_attempts=3,
        )
        created = pipeline.run(initial)
        repeated = pipeline.run(select(corpus.scan(FIRST, previous), initial.source.id))

        source_path = root / initial.canonical_key
        source_path.write_text("# Updated tracing\n\nNew span guidance.\n")
        changed_events = corpus.scan(SECOND, previous)
        changed = select(changed_events, initial.source.id)
        updated = pipeline.run(changed)
        changed_cursor = {event.source.id: event for event in changed_events}

        source_path.unlink()
        deleted = pipeline.run(
            select(corpus.scan(SECOND, changed_cursor), initial.source.id)
        )

        document = repository.get_document_by_key(
            "alpha", initial.source.id, initial.canonical_key
        )
        versions = repository.list_versions("alpha", created.document_id)
        stored_versions = [
            repository.get_version("alpha", version.id) for version in versions
        ]

    assert (
        created.job.state
        == repeated.job.state
        == updated.job.state
        == JobState.COMPLETED
    )
    assert created.job.id == repeated.job.id
    assert created.version_id != updated.version_id
    assert len(versions) == 2
    assert all(version is not None for version in stored_versions)
    assert all(
        version.raw_ref.startswith("alpha/raw/")
        for version in stored_versions
        if version
    )
    assert all(
        version.normalized_ref.startswith("alpha/normalized/")
        for version in stored_versions
        if version
    )
    assert all(record.tenant_id == "alpha" for record in sink.records.values())
    assert {record.document_version_id for record in sink.records.values()} == {
        created.version_id,
        updated.version_id,
    }
    assert all(record.start < record.end for record in sink.records.values())
    assert deleted.kind == "deleted"
    assert document is not None
    assert document.deleted is True
    assert document.current_version_id is None
