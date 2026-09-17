"""Return typed evidence from the authorized BM25 baseline."""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from adapters.opensearch.search import SearchRequest, SearchResult
from modules.policy.authorizer import (
    AuthorizationReason,
    Authorizer,
    Principal,
    ResourceAction,
)


class SearchAdapter(Protocol):
    def search(self, request: SearchRequest) -> SearchResult: ...


class RetrievalUnavailable(RuntimeError):
    """The search dependency could not return evidence safely."""


@dataclass(frozen=True)
class QueryContext:
    query: str
    principal: Principal
    limit: int = 10
    offset: int = 0
    source_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    include_historical: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip() or len(self.query) > 1000:
            raise ValueError("invalid query")
        if not 1 <= self.limit <= 100:
            raise ValueError("invalid limit")
        if not 0 <= self.offset <= 10_000:
            raise ValueError("invalid offset")


@dataclass(frozen=True)
class Evidence:
    chunk_id: str
    tenant_id: str
    source_id: str
    document_id: str
    document_version_id: str
    ordinal: int
    text: str
    span: tuple[int, int]
    content_hash: str
    parser_version: str
    chunker_version: str
    source_timestamp: datetime
    is_current: bool
    score: float
    authorization_reason: AuthorizationReason


@dataclass(frozen=True)
class EvidenceSet:
    evidence: tuple[Evidence, ...]
    strategy: str
    total: int
    took_ms: int


class BM25Retriever:
    def __init__(self, adapter: SearchAdapter, authorizer: Authorizer) -> None:
        self.adapter = adapter
        self.authorizer = authorizer

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        if not context.principal.known:
            return EvidenceSet((), "bm25", 0, 0)
        access = (
            "public",
            f"principal:{context.principal.id}",
            *(f"role:{role}" for role in context.principal.roles),
            *(f"group:{group}" for group in context.principal.groups),
        )
        try:
            result = self.adapter.search(
                SearchRequest(
                    context.query,
                    context.principal.tenant_id,
                    access,
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
            decision = self.authorizer.authorize(
                context.principal,
                ResourceAction(hit.tenant_id, hit.document_id, "read", hit.policy),
            )
            if not decision.allowed:
                continue
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
        return EvidenceSet(tuple(evidence), "bm25", len(evidence), result.took_ms)
