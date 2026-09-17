"""The public BM25 and dense retriever seams compose through hybrid fusion."""

from datetime import UTC, datetime

import pytest

from adapters.opensearch.dense import (
    DenseSearchHit,
    DenseSearchRequest,
    DenseSearchResult,
)
from adapters.opensearch.search import SearchHit, SearchRequest, SearchResult
from modules.embeddings.embedder import EmbeddingRecord
from modules.policy.authorizer import Authorizer, Principal
from modules.retrieval.dense import DenseRetriever
from modules.retrieval.hybrid import HybridRetriever
from modules.retrieval.retriever import BM25Retriever, QueryContext

STAMP = datetime(2026, 9, 17, tzinfo=UTC)


class LexicalAdapter:
    def search(self, request: SearchRequest) -> SearchResult:
        return SearchResult(
            (
                SearchHit(
                    "shared",
                    "alpha",
                    "otel",
                    "doc",
                    "v1",
                    ("public",),
                    0,
                    "shared evidence",
                    0,
                    15,
                    "hash",
                    "parser",
                    "chunker",
                    STAMP,
                    True,
                    9.0,
                ),
            ),
            1,
            2,
        )


class DenseAdapter:
    def search(self, request: DenseSearchRequest) -> DenseSearchResult:
        return DenseSearchResult(
            (
                DenseSearchHit(
                    "shared",
                    "alpha",
                    "otel",
                    "doc",
                    "v1",
                    ("public",),
                    0,
                    "shared evidence",
                    0,
                    15,
                    "hash",
                    "parser",
                    "chunker",
                    STAMP,
                    True,
                    0.9,
                ),
            ),
            1,
            3,
        )


class QueryEmbedder:
    def embed_query(self, query: str) -> EmbeddingRecord:
        return EmbeddingRecord((1.0, 0.0), "fake", "v1", 2, "hash", "query", 1)


@pytest.mark.integration
def test_hybrid_composes_public_retrievers_without_duplicate_evidence() -> None:
    authorizer = Authorizer()
    hybrid = HybridRetriever(
        BM25Retriever(LexicalAdapter(), authorizer),
        DenseRetriever(DenseAdapter(), QueryEmbedder(), authorizer, "dataset-v1"),
        authorizer,
    )

    result = hybrid.retrieve(QueryContext("query", Principal("alice", "alpha", (), ())))

    assert [item.chunk_id for item in result.evidence] == ["shared"]
    assert result.evidence[0].score == pytest.approx(2 / 61)
    assert result.took_ms == 5
