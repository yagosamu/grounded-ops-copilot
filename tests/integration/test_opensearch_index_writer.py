"""Document-version projections stay consistent across OpenSearch bulk writes."""

from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from typing import cast
from uuid import uuid4

import pytest
from opensearchpy import OpenSearch

from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.opensearch.index_writer import (
    BulkProjectionError,
    IndexChunk,
    OpenSearchIndexWriter,
    VersionProjection,
)
from adapters.policy.snapshot import SnapshotPolicyStore
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer


@pytest.fixture
def writer(opensearch_client: OpenSearch) -> Iterator[OpenSearchIndexWriter]:
    prefix = f"test-writer-{uuid4().hex}"
    schema = LexicalIndexSchema(opensearch_client, prefix)
    schema.ensure("1")
    policies = tuple(
        DocumentPolicy(
            "alpha",
            "source-1",
            "document-1",
            version_id,
            ("role:engineer", "group:oncall"),
            False,
        )
        for version_id in (None, "version-1", "version-2")
    )
    yield OpenSearchIndexWriter(
        opensearch_client,
        schema.write_alias,
        PolicyEnforcer(SnapshotPolicyStore(policies)),
    )
    opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)


def projection(version: str, *texts: str) -> VersionProjection:
    return VersionProjection(
        tenant_id="alpha",
        source_id="source-1",
        document_id="document-1",
        document_version_id=version,
        policy=("role:engineer", "group:oncall"),
        source_timestamp=datetime(2026, 9, 17, tzinfo=UTC),
        chunks=tuple(
            IndexChunk(
                chunk_id=f"{version}-{ordinal}",
                ordinal=ordinal,
                text=text,
                start=ordinal * 10,
                end=ordinal * 10 + len(text),
                content_hash=f"hash-{ordinal}",
                parser_version="markdown-v1",
                chunker_version="structural-v1",
            )
            for ordinal, text in enumerate(texts)
        ),
    )


@pytest.mark.integration
def test_upsert_repeat_and_version_replacement(
    writer: OpenSearchIndexWriter, opensearch_client: OpenSearch
) -> None:
    old = projection("version-1", "old evidence", "shared evidence")
    writer.upsert(old)
    writer.upsert(old)
    writer.upsert(projection("version-2", "new evidence"))

    response = opensearch_client.search(
        index=writer.write_alias,
        body={"query": {"term": {"document_id": "document-1"}}, "size": 10},
    )
    hits = {hit["_id"]: hit["_source"] for hit in response["hits"]["hits"]}
    assert len(hits) == 3
    assert hits["version-1-0"]["is_current"] is False
    assert hits["version-1-1"]["is_current"] is False
    assert hits["version-2-0"]["is_current"] is True
    assert hits["version-2-0"]["policy"] == ["role:engineer", "group:oncall"]


@pytest.mark.integration
def test_remove_document_tombstones_active_projection(
    writer: OpenSearchIndexWriter, opensearch_client: OpenSearch
) -> None:
    writer.upsert(projection("version-1", "private evidence"))

    writer.remove_document("alpha", "document-1")

    response = opensearch_client.count(
        index=writer.write_alias, body={"query": {"match_all": {}}}
    )
    assert response["count"] == 0


@pytest.mark.integration
def test_partial_bulk_failure_preserves_previous_current_version(
    writer: OpenSearchIndexWriter, opensearch_client: OpenSearch
) -> None:
    old = projection("version-1", "current evidence")
    writer.upsert(old)
    invalid = replace(
        projection("version-2", "valid replacement", "invalid replacement"),
        chunks=(
            projection("version-2", "valid replacement").chunks[0],
            replace(
                projection("version-2", "invalid replacement").chunks[0],
                chunk_id="version-2-invalid",
                ordinal=cast(int, "not-an-integer"),
            ),
        ),
    )

    with pytest.raises(BulkProjectionError) as error:
        writer.upsert(invalid)

    response = opensearch_client.search(
        index=writer.write_alias,
        body={"query": {"term": {"document_id": "document-1"}}, "size": 10},
    )
    hits = response["hits"]["hits"]
    assert error.value.failed_chunk_ids == ("version-2-invalid",)
    assert [(hit["_id"], hit["_source"]["is_current"]) for hit in hits] == [
        ("version-1-0", True)
    ]
