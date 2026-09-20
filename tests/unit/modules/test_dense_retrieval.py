"""Dense retrieval preserves the BM25 authorization and corpus context."""

from datetime import UTC, datetime

import pytest

from adapters.opensearch.dense import (
    DenseSearchHit,
    DenseSearchRequest,
    DenseSearchResult,
)
from adapters.policy.snapshot import SnapshotPolicyStore
from modules.embeddings.embedder import EmbeddingRecord
from modules.policy.authorizer import Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.dense import DenseRetriever
from modules.retrieval.retriever import QueryContext, RetrievalUnavailable


class FrozenQueryEmbedder:
    def embed_query(self, query: str) -> EmbeddingRecord:
        return EmbeddingRecord(
            (1.0, 0.0),
            "text-embedding-3-small",
            "2026-09-01",
            2,
            "query-hash",
            "query",
            3,
        )


class FrozenDenseAdapter:
    def __init__(self, result: DenseSearchResult | Exception) -> None:
        self.result = result
        self.requests: list[DenseSearchRequest] = []

    def search(self, request: DenseSearchRequest) -> DenseSearchResult:
        self.requests.append(request)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def hit(
    *, tenant: str = "alpha", policy: tuple[str, ...] = ("public",)
) -> DenseSearchHit:
    return DenseSearchHit(
        "chunk-1",
        tenant,
        "otel-tracing-api",
        "doc-1",
        "version-1",
        policy,
        0,
        "TracerProvider provides access to tracers",
        0,
        42,
        "content-hash",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        0.97,
    )


def enforcer(*hits: DenseSearchHit) -> PolicyEnforcer:
    return PolicyEnforcer(
        SnapshotPolicyStore(
            tuple(
                DocumentPolicy(
                    item.tenant_id,
                    item.source_id,
                    item.document_id,
                    item.document_version_id,
                    item.policy,
                    False,
                )
                for item in hits
            )
        )
    )


@pytest.mark.unit
def test_dense_uses_the_same_policy_filters_and_corpus_as_bm25() -> None:
    adapter = FrozenDenseAdapter(DenseSearchResult((hit(),), 1, 8))
    retriever = DenseRetriever(
        adapter,
        FrozenQueryEmbedder(),
        enforcer(hit()),
        corpus_version="dataset-v1",
    )
    principal = Principal("alice", "alpha", ("engineer",), ("oncall",))

    result = retriever.retrieve(
        QueryContext(
            "Which component provides tracers?",
            principal,
            source_ids=("otel-tracing-api",),
            document_ids=("doc-1",),
        )
    )

    assert adapter.requests == [
        DenseSearchRequest(
            (1.0, 0.0),
            "alpha",
            ("public", "principal:alice", "role:engineer", "group:oncall"),
            "dataset-v1",
            10,
            0,
            ("otel-tracing-api",),
            ("doc-1",),
            False,
        )
    ]
    assert result.strategy == "dense-candidate"
    assert result.evidence[0].chunk_id == "chunk-1"
    assert result.evidence[0].tenant_id == "alpha"
    assert result.evidence[0].score == 0.97


@pytest.mark.unit
def test_dense_reauthorizes_hits_and_redacts_dependency_failure() -> None:
    cross_tenant = FrozenDenseAdapter(DenseSearchResult((hit(tenant="beta"),), 1, 2))
    context = QueryContext("query", Principal("alice", "alpha", (), ()))

    denied = DenseRetriever(
        cross_tenant,
        FrozenQueryEmbedder(),
        enforcer(hit(tenant="beta")),
        "dataset-v1",
    ).retrieve(context)
    unavailable = DenseRetriever(
        FrozenDenseAdapter(RuntimeError("private endpoint")),
        FrozenQueryEmbedder(),
        enforcer(),
        "dataset-v1",
    )

    assert denied.evidence == ()
    with pytest.raises(RetrievalUnavailable, match="retrieval unavailable"):
        unavailable.retrieve(context)
