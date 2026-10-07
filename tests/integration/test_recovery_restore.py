"""Witness a clean local restore from PostgreSQL and object-store backups."""

import json
import os
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import boto3
import pytest
from botocore.config import Config
from mypy_boto3_s3 import S3Client
from opensearchpy import OpenSearch
from sqlalchemy import Engine

from adapters.object_store.document_store import DocumentStore
from adapters.postgres.ingestion_repository import IngestionRepository
from adapters.sources.markdown import SourceEvent
from domain.ingestion import Source
from modules.chunking.structural import StructuralChunker
from modules.ingestion.pipeline import IngestionPipeline, PreparedChunk
from modules.parsing.parser import MarkdownParser
from ops.recovery.exercise import run_local_recovery

pytestmark = [pytest.mark.integration, pytest.mark.operational]


class NoopSink:
    def prepare(self, chunks: tuple[PreparedChunk, ...]) -> None:
        pass


def test_clean_restore_verifies_authoritative_data_and_rebuilt_search(
    database: Engine,
    s3_client: S3Client,
    artifact_bucket: str,
    opensearch_client: OpenSearch,
    tmp_path: Path,
) -> None:
    content = b"# On-call recovery\n\nRestart the collector after checking backlog.\n"
    source = Source(
        "runbook", "alpha", "markdown", "https://example.org/runbook", ("role:oncall",)
    )
    event = SourceEvent(
        "new",
        source,
        "runbook.md",
        sha256(content).hexdigest(),
        datetime(2026, 1, 1, tzinfo=UTC),
        content,
        "synthetic",
        True,
    )
    with database.begin() as connection:
        outcome = IngestionPipeline(
            IngestionRepository(connection),
            DocumentStore(s3_client, artifact_bucket),
            MarkdownParser(),
            StructuralChunker(max_tokens=80),
            NoopSink(),
            max_attempts=2,
        ).run(event)
    assert outcome.kind == "completed"

    report_path = Path(
        os.environ.get("RECOVERY_REPORT_PATH", tmp_path / "recovery.json")
    )
    report = run_local_recovery(
        database,
        DocumentStore(s3_client, artifact_bucket),
        s3_client,
        opensearch_client,
        report_path,
        chunk_max_tokens=80,
    )
    saved = json.loads(report_path.read_text(encoding="utf-8"))
    assert saved == report
    assert report["result"] == "pass"
    assert report["environment"] == "local-compose"
    assert report["metadata_checksums_match"] is True
    assert report["artifact_checksums_match"] is True
    assert report["restored_documents"] == 1
    assert report["restored_versions"] == 1
    assert report["restored_chunks"] == 1
    assert report["rpo_seconds"] == 0
    assert 0 < report["rto_seconds"] <= 14_400
    assert report["rpo_seconds"] <= report["rpo_target_seconds"] == 86_400
    assert report["rto_seconds"] <= report["rto_target_seconds"] == 14_400
    assert report["search_evidence"][0]["text_sha256"] == sha256(content).hexdigest()
    assert report["search_evidence"][0]["tenant_id"] == "alpha"
    assert report["search_evidence"][0]["policy"] == ["role:oncall"]
    assert report["search_evidence"][0]["document_version_id"] == outcome.version_id


def test_local_exercise_rejects_a_non_compose_store_before_creating_backups(
    database: Engine,
    opensearch_client: OpenSearch,
    tmp_path: Path,
) -> None:
    external_client = boto3.client(
        "s3",
        endpoint_url="http://127.0.0.2:1",
        aws_access_key_id="placeholder",
        aws_secret_access_key="placeholder",
        region_name="us-east-1",
        config=Config(
            connect_timeout=2, read_timeout=5, retries={"total_max_attempts": 1}
        ),
    )
    try:
        with pytest.raises(ValueError, match="local Compose dependencies"):
            run_local_recovery(
                database,
                DocumentStore(external_client, "forbidden"),
                external_client,
                opensearch_client,
                tmp_path / "never-written.json",
                chunk_max_tokens=80,
            )
        assert not (tmp_path / "never-written.json").exists()
    finally:
        external_client.close()
