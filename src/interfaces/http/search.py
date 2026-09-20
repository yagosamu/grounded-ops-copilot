"""Authenticated HTTP interface for authorized evidence search."""

from datetime import datetime
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from interfaces.http.auth import PrincipalDependency
from modules.policy.authorizer import Principal
from modules.policy.quotas import (
    QuotaEnforcer,
    QuotaLease,
    QuotaOperation,
    QuotaOutcome,
)
from modules.retrieval.retriever import EvidenceSet, QueryContext, RetrievalUnavailable


class Retriever(Protocol):
    def retrieve(self, context: QueryContext) -> EvidenceSet: ...


class EvidenceResponse(BaseModel):
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
    authorization_reason: str


class DiagnosticsResponse(BaseModel):
    strategy: str
    total: int
    took_ms: int


class SearchResponse(BaseModel):
    evidence: list[EvidenceResponse]
    diagnostics: DiagnosticsResponse


def _response(result: EvidenceSet) -> SearchResponse:
    return SearchResponse(
        evidence=[
            EvidenceResponse(
                chunk_id=item.chunk_id,
                tenant_id=item.tenant_id,
                source_id=item.source_id,
                document_id=item.document_id,
                document_version_id=item.document_version_id,
                ordinal=item.ordinal,
                text=item.text,
                span=item.span,
                content_hash=item.content_hash,
                parser_version=item.parser_version,
                chunker_version=item.chunker_version,
                source_timestamp=item.source_timestamp,
                is_current=item.is_current,
                score=item.score,
                authorization_reason=item.authorization_reason.value,
            )
            for item in result.evidence
        ],
        diagnostics=DiagnosticsResponse(
            strategy=result.strategy, total=result.total, took_ms=result.took_ms
        ),
    )


def create_search_router(
    retriever: Retriever,
    authenticate: PrincipalDependency,
    *,
    quotas: QuotaEnforcer | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/v1/evidence", tags=["evidence"])

    @router.get("/search", response_model=SearchResponse)
    def search(
        q: Annotated[str, Query(min_length=1, max_length=1000)],
        principal: Annotated[Principal, Depends(authenticate)],
        limit: Annotated[int, Query(ge=1, le=100)] = 10,
        offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
        source_id: Annotated[list[str] | None, Query()] = None,
        document_id: Annotated[list[str] | None, Query()] = None,
        include_historical: bool = False,
    ) -> SearchResponse:
        lease = _reserve(quotas, principal, QuotaOperation.SEARCH)
        try:
            result = retriever.retrieve(
                QueryContext(
                    q,
                    principal,
                    limit,
                    offset,
                    tuple(source_id or ()),
                    tuple(document_id or ()),
                    include_historical,
                )
            )
        except RetrievalUnavailable as error:
            raise HTTPException(
                status_code=503, detail="retrieval temporarily unavailable"
            ) from error
        finally:
            if quotas is not None:
                quotas.release(lease)
        return _response(result)

    return router


def _reserve(
    quotas: QuotaEnforcer | None,
    principal: Principal,
    operation: QuotaOperation,
) -> QuotaLease | None:
    if quotas is None:
        return None
    decision = quotas.acquire(principal, operation)
    if decision.outcome is QuotaOutcome.BACKEND_UNAVAILABLE:
        raise HTTPException(status_code=503, detail="quota temporarily unavailable")
    if decision.outcome is not QuotaOutcome.ALLOWED:
        raise HTTPException(
            status_code=429,
            detail="quota exceeded",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )
    return decision.lease
