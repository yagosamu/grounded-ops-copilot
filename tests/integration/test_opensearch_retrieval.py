"""OpenSearch executes BM25 and mandatory authorization/filter DSL."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from opensearchpy import OpenSearch

from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.opensearch.index_writer import (
    IndexChunk,
    OpenSearchIndexWriter,
    VersionProjection,
)
from adapters.opensearch.search import OpenSearchBM25Adapter
from modules.policy.authorizer import Authorizer, Principal
from modules.retrieval.retriever import (
    BM25Retriever,
    QueryContext,
    RetrievalUnavailable,
)


@pytest.fixture
def retriever(opensearch_client: OpenSearch) -> Iterator[BM25Retriever]:
    prefix = f"test-retrieval-{uuid4().hex}"
    schema = LexicalIndexSchema(opensearch_client, prefix)
    schema.ensure("1")
    writer = OpenSearchIndexWriter(opensearch_client, schema.write_alias)
    writer.upsert(version("alpha", "doc-trace", "v1", "Tracing overview", "public"))
    writer.upsert(
        version(
            "alpha",
            "doc-trace",
            "v2",
            "TracerProvider provides tracer access",
            "public",
        )
    )
    writer.upsert(
        version(
            "alpha",
            "doc-resource",
            "v1",
            "Resource describes an entity",
            "group:oncall",
        )
    )
    writer.upsert(
        version("beta", "doc-secret", "v1", "TracerProvider private incident", "public")
    )
    yield BM25Retriever(
        OpenSearchBM25Adapter(opensearch_client, schema.read_alias), Authorizer()
    )
    opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)


def version(
    tenant: str, document: str, version_id: str, text: str, policy: str
) -> VersionProjection:
    chunk_id = f"{tenant}-{document}-{version_id}"
    return VersionProjection(
        tenant_id=tenant,
        source_id="otel" if document.startswith("doc-trace") else "runbooks",
        document_id=document,
        document_version_id=version_id,
        policy=(policy,),
        source_timestamp=datetime(2026, 9, 17, tzinfo=UTC),
        chunks=(
            IndexChunk(
                chunk_id,
                0,
                text,
                0,
                len(text),
                f"hash-{chunk_id}",
                "markdown-v1",
                "structural-v1",
            ),
        ),
    )


@pytest.mark.integration
def test_bm25_orders_relevant_authorized_current_evidence(
    retriever: BM25Retriever,
) -> None:
    result = retriever.retrieve(
        QueryContext(
            "TracerProvider tracer access",
            Principal("alice", "alpha", ("engineer",), ()),
        )
    )

    assert [e.document_version_id for e in result.evidence] == ["v2"]
    assert result.evidence[0].text == "TracerProvider provides tracer access"
    assert result.evidence[0].tenant_id == "alpha"


@pytest.mark.integration
def test_applies_metadata_acl_and_historical_filters(retriever: BM25Retriever) -> None:
    oncall = Principal("alice", "alpha", (), ("oncall",))

    filtered = retriever.retrieve(
        QueryContext("entity", oncall, source_ids=("runbooks",))
    )
    historical = retriever.retrieve(
        QueryContext(
            "Tracing",
            oncall,
            document_ids=("doc-trace",),
            include_historical=True,
        )
    )

    assert [e.document_id for e in filtered.evidence] == ["doc-resource"]
    assert [(e.document_version_id, e.is_current) for e in historical.evidence] == [
        ("v1", False)
    ]


@pytest.mark.integration
def test_empty_and_real_dependency_failure_are_explicit(
    retriever: BM25Retriever,
) -> None:
    empty = retriever.retrieve(
        QueryContext("no-such-term", Principal("alice", "alpha", (), ()))
    )
    unavailable = BM25Retriever(
        OpenSearchBM25Adapter(
            OpenSearch(hosts=[{"host": "127.0.0.1", "port": 1}], max_retries=0),
            "missing",
        ),
        Authorizer(),
    )

    assert empty.evidence == ()
    with pytest.raises(RetrievalUnavailable, match="retrieval unavailable"):
        unavailable.retrieve(QueryContext("query", Principal("alice", "alpha", (), ())))
