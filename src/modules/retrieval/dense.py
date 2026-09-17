"""Dense retrieval candidate using the baseline policy context unchanged."""

from typing import Protocol

from adapters.opensearch.dense import DenseSearchRequest, DenseSearchResult
from modules.embeddings.embedder import EmbeddingRecord
from modules.policy.authorizer import Authorizer, ResourceAction
from modules.retrieval.retriever import (
    Evidence,
    EvidenceSet,
    QueryContext,
    RetrievalUnavailable,
)


class QueryEmbedder(Protocol):
    def embed_query(self, query: str) -> EmbeddingRecord: ...


class DenseSearchAdapter(Protocol):
    def search(self, request: DenseSearchRequest) -> DenseSearchResult: ...


class DenseRetriever:
    def __init__(
        self,
        adapter: DenseSearchAdapter,
        embedder: QueryEmbedder,
        authorizer: Authorizer,
        corpus_version: str,
    ) -> None:
        if not corpus_version:
            raise ValueError("invalid corpus version")
        self._adapter = adapter
        self._embedder = embedder
        self._authorizer = authorizer
        self._corpus_version = corpus_version

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        if not context.principal.known:
            return EvidenceSet((), "dense-candidate", 0, 0)
        access = (
            "public",
            f"principal:{context.principal.id}",
            *(f"role:{role}" for role in context.principal.roles),
            *(f"group:{group}" for group in context.principal.groups),
        )
        try:
            embedding = self._embedder.embed_query(context.query)
            result = self._adapter.search(
                DenseSearchRequest(
                    embedding.vector,
                    context.principal.tenant_id,
                    access,
                    self._corpus_version,
                    context.limit,
                    context.offset,
                    context.source_ids,
                    context.document_ids,
                    context.include_historical,
                )
            )
        except Exception as error:
            raise RetrievalUnavailable("retrieval unavailable") from error
        evidence: list[Evidence] = []
        for hit in result.hits:
            decision = self._authorizer.authorize(
                context.principal,
                ResourceAction(hit.tenant_id, hit.document_id, "read", hit.policy),
            )
            if decision.allowed:
                evidence.append(
                    Evidence(
                        hit.chunk_id,
                        hit.tenant_id,
                        hit.source_id,
                        hit.document_id,
                        hit.document_version_id,
                        hit.ordinal,
                        hit.text,
                        (hit.start, hit.end),
                        hit.content_hash,
                        hit.parser_version,
                        hit.chunker_version,
                        hit.source_timestamp,
                        hit.is_current,
                        hit.score,
                        decision.reason,
                    )
                )
        return EvidenceSet(
            tuple(evidence), "dense-candidate", len(evidence), result.took_ms
        )
