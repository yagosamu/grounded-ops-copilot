"""Dense retrieval candidate using the baseline policy context unchanged."""

from typing import Protocol

from adapters.opensearch.dense import DenseSearchRequest, DenseSearchResult
from modules.embeddings.embedder import EmbeddingRecord
from modules.policy.enforcement import DocumentRef, PolicyEnforcer
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
        enforcer: PolicyEnforcer,
        corpus_version: str,
    ) -> None:
        if not corpus_version:
            raise ValueError("invalid corpus version")
        self._adapter = adapter
        self._embedder = embedder
        self._enforcer = enforcer
        self._corpus_version = corpus_version

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        scope = self._enforcer.search_scope(context.principal)
        if scope is None:
            return EvidenceSet((), "dense-candidate", 0, 0)
        try:
            embedding = self._embedder.embed_query(context.query)
            result = self._adapter.search(
                DenseSearchRequest(
                    embedding.vector,
                    scope.tenant_id,
                    scope.access_policy,
                    self._corpus_version,
                    context.limit,
                    context.offset,
                    context.source_ids,
                    context.document_ids,
                    context.include_historical,
                )
            )
            references = tuple(
                DocumentRef(hit.tenant_id, hit.document_id, hit.document_version_id)
                for hit in result.hits
            )
            authorized = self._enforcer.authorize_reads(context.principal, references)
        except Exception as error:
            raise RetrievalUnavailable("retrieval unavailable") from error
        evidence: list[Evidence] = []
        for hit in result.hits:
            reference = DocumentRef(
                hit.tenant_id, hit.document_id, hit.document_version_id
            )
            document = authorized.get(reference)
            if document is not None:
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
                        document.decision.reason,
                    )
                )
        return EvidenceSet(
            tuple(evidence), "dense-candidate", len(evidence), result.took_ms
        )
