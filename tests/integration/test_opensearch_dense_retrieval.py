"""OpenSearch executes vector retrieval with mandatory policy filters."""

from collections.abc import Iterator
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from opensearchpy import OpenSearch

from adapters.opensearch.dense import DenseDocument, OpenSearchDenseAdapter
from modules.embeddings.embedder import EmbeddingRecord
from modules.policy.authorizer import Authorizer, Principal
from modules.retrieval.dense import DenseRetriever
from modules.retrieval.retriever import QueryContext


class FixedQueryEmbedder:
    def embed_query(self, query: str) -> EmbeddingRecord:
        return EmbeddingRecord((1.0, 0.0), "fake-v1", "v1", 2, "query-hash", "query", 1)


@pytest.fixture
def dense_retriever(opensearch_client: OpenSearch) -> Iterator[DenseRetriever]:
    index = f"test-dense-{uuid4().hex}"
    adapter = OpenSearchDenseAdapter(opensearch_client, index, dimensions=2)
    adapter.ensure_index()
    timestamp = datetime(2026, 9, 17, tzinfo=UTC)
    adapter.index(
        (
            DenseDocument(
                "trace",
                "alpha",
                "otel-tracing-api",
                "doc-trace",
                "v1",
                ("public",),
                0,
                "TracerProvider provides tracers",
                0,
                30,
                "trace-hash",
                "markdown-v1",
                "structural-v1",
                timestamp,
                True,
                "dataset-v1",
                "fake-v1",
                "v1",
                (1.0, 0.0),
            ),
            DenseDocument(
                "resource",
                "alpha",
                "otel-resource",
                "doc-resource",
                "v1",
                ("group:oncall",),
                0,
                "Resource represents an entity",
                0,
                29,
                "resource-hash",
                "markdown-v1",
                "structural-v1",
                timestamp,
                True,
                "dataset-v1",
                "fake-v1",
                "v1",
                (0.0, 1.0),
            ),
            DenseDocument(
                "secret",
                "beta",
                "secret-source",
                "secret-doc",
                "v1",
                ("public",),
                0,
                "Private incident",
                0,
                16,
                "secret-hash",
                "markdown-v1",
                "structural-v1",
                timestamp,
                True,
                "dataset-v1",
                "fake-v1",
                "v1",
                (1.0, 0.0),
            ),
        )
    )
    yield DenseRetriever(adapter, FixedQueryEmbedder(), Authorizer(), "dataset-v1")
    opensearch_client.indices.delete(index=index, ignore_unavailable=True)


@pytest.mark.integration
def test_dense_orders_only_authorized_evidence_from_the_frozen_corpus(
    dense_retriever: DenseRetriever,
) -> None:
    public = dense_retriever.retrieve(
        QueryContext("tracers", Principal("alice", "alpha", (), ()))
    )
    oncall = dense_retriever.retrieve(
        QueryContext("entity", Principal("alice", "alpha", (), ("oncall",)))
    )

    assert [item.chunk_id for item in public.evidence] == ["trace"]
    assert [item.chunk_id for item in oncall.evidence] == ["trace", "resource"]
    assert {item.tenant_id for item in oncall.evidence} == {"alpha"}
