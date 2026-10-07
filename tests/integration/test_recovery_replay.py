"""A recovered index is derived from durable, tenant-scoped evidence."""

from datetime import UTC, datetime
from hashlib import sha256
from uuid import uuid4

import pytest
from mypy_boto3_s3 import S3Client
from opensearchpy import OpenSearch
from sqlalchemy import Engine

from adapters.object_store.document_store import ArtifactError, DocumentStore
from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.postgres.ingestion_repository import IngestionRepository
from adapters.sources.markdown import SourceEvent
from domain.ingestion import Source
from modules.chunking.structural import StructuralChunker
from modules.indexing.rebuild import IndexRebuilder
from modules.indexing.replay import replay_authoritative
from modules.ingestion.pipeline import IngestionPipeline, PreparedChunk
from modules.parsing.parser import MarkdownParser

pytestmark = [pytest.mark.integration, pytest.mark.operational]


class NoopSink:
    def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
        pass


def _event(
    tenant: str, document: str, content: bytes, timestamp: datetime
) -> SourceEvent:
    source = Source(
        f"source-{tenant}", tenant, "markdown", "https://example.org", ("role:oncall",)
    )
    return SourceEvent(
        "new",
        source,
        document,
        sha256(content).hexdigest(),
        timestamp,
        content,
        "synthetic",
        True,
    )


def test_replay_preserves_current_history_tenants_and_deletions(
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
    opensearch_client: OpenSearch,
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    first = datetime(2026, 1, 1, tzinfo=UTC)
    second = datetime(2026, 2, 1, tzinfo=UTC)
    with database.begin() as connection:
        pipeline = IngestionPipeline(
            IngestionRepository(connection),
            store,
            MarkdownParser(),
            StructuralChunker(max_tokens=80),
            NoopSink(),
            max_attempts=2,
        )
        alpha_old = pipeline.run(
            _event("alpha", "runbook.md", b"# Recovery\n\nOld path.\n", first)
        )
        alpha_new = pipeline.run(
            _event("alpha", "runbook.md", b"# Recovery\n\nNew path.\n", second)
        )
        beta = pipeline.run(
            _event("beta", "guide.md", b"# Guide\n\nBeta only.\n", first)
        )
        removed = pipeline.run(_event("alpha", "removed.md", b"# Gone\n", first))
        assert removed.document_id is not None
        connection_repo = IngestionRepository(connection)
        connection_repo.delete("alpha", removed.document_id)

    prefix = f"test-replay-{uuid4().hex}"
    schema = LexicalIndexSchema(opensearch_client, prefix, replicas=0)
    schema.ensure("1")
    candidate = schema.create_candidate("2")
    try:
        summary = replay_authoritative(
            database, store, opensearch_client, candidate, chunk_max_tokens=80
        )
        result = opensearch_client.search(
            index=candidate, body={"query": {"match_all": {}}, "size": 10}
        )
        hits = [item["_source"] for item in result["hits"]["hits"]]
        assert summary.documents == 2
        assert summary.versions == 3
        assert summary.chunks == 3
        assert {
            (hit["tenant_id"], hit["document_version_id"], hit["is_current"])
            for hit in hits
        } == {
            ("alpha", alpha_old.version_id, False),
            ("alpha", alpha_new.version_id, True),
            ("beta", beta.version_id, True),
        }
        assert all(hit["policy"] == ["role:oncall"] for hit in hits)
        assert schema.active_index() == schema.index_name("1")
    finally:
        opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)


def test_corrupt_authoritative_artifact_blocks_promotion(
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
    opensearch_client: OpenSearch,
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    with database.begin() as connection:
        repository = IngestionRepository(connection)
        outcome = IngestionPipeline(
            repository,
            store,
            MarkdownParser(),
            StructuralChunker(max_tokens=80),
            NoopSink(),
            max_attempts=2,
        ).run(
            _event(
                "alpha", "runbook.md", b"# Verified\n", datetime(2026, 1, 1, tzinfo=UTC)
            )
        )
        assert outcome.version_id is not None
        version = repository.get_version("alpha", outcome.version_id)
        assert version is not None and version.normalized_ref is not None
        normalized_key = version.normalized_ref
    s3_client.put_object(Bucket=artifact_bucket, Key=normalized_key, Body=b"corrupt")

    prefix = f"test-corrupt-replay-{uuid4().hex}"
    schema = LexicalIndexSchema(opensearch_client, prefix, replicas=0)
    previous = schema.ensure("1")
    try:
        with pytest.raises(ArtifactError, match="artifact integrity failed"):
            IndexRebuilder(schema).rebuild(
                "2",
                build=lambda candidate: replay_authoritative(
                    database, store, opensearch_client, candidate, chunk_max_tokens=80
                ),
                validate=lambda candidate: True,
            )
        assert schema.active_index() == previous
        assert opensearch_client.count(index=schema.read_alias)["count"] == 0
    finally:
        opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)


def test_completed_empty_document_does_not_block_recovery(
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
    opensearch_client: OpenSearch,
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    with database.begin() as connection:
        outcome = IngestionPipeline(
            IngestionRepository(connection),
            store,
            MarkdownParser(),
            StructuralChunker(max_tokens=80),
            NoopSink(),
            max_attempts=2,
        ).run(_event("alpha", "empty.md", b"", datetime(2026, 1, 1, tzinfo=UTC)))
    assert outcome.kind == "completed"
    prefix = f"test-empty-replay-{uuid4().hex}"
    schema = LexicalIndexSchema(opensearch_client, prefix, replicas=0)
    candidate = schema.create_candidate("2")
    try:
        summary = replay_authoritative(
            database, store, opensearch_client, candidate, chunk_max_tokens=80
        )
        assert (summary.documents, summary.versions, summary.chunks) == (1, 1, 0)
        assert opensearch_client.count(index=candidate)["count"] == 0
    finally:
        opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)
