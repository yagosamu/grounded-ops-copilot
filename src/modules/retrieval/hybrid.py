"""Deterministic reciprocal-rank fusion candidate."""

from dataclasses import replace
from typing import Protocol

from modules.policy.enforcement import DocumentRef, PolicyEnforcer
from modules.retrieval.retriever import (
    Evidence,
    EvidenceSet,
    QueryContext,
    RetrievalUnavailable,
)


class Retriever(Protocol):
    def retrieve(self, context: QueryContext) -> EvidenceSet: ...


class HybridRetriever:
    def __init__(
        self,
        lexical: Retriever,
        dense: Retriever,
        enforcer: PolicyEnforcer,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        self._lexical = lexical
        self._dense = dense
        self._enforcer = enforcer
        self._rrf_k = rrf_k

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        lexical, _ = self._retrieve(self._lexical, context)
        dense, dense_failure = self._retrieve(self._dense, context)
        if lexical is None and dense is None:
            raise RetrievalUnavailable("retrieval unavailable")

        scores: dict[tuple[str, str], float] = {}
        evidence_by_identity: dict[tuple[str, str], Evidence] = {}
        for result in (lexical, dense):
            if result is None:
                continue
            for rank, evidence in enumerate(result.evidence, 1):
                identity = (evidence.chunk_id, evidence.document_version_id)
                scores[identity] = scores.get(identity, 0.0) + 1 / (self._rrf_k + rank)
                evidence_by_identity.setdefault(identity, evidence)

        try:
            references = tuple(
                DocumentRef(item.tenant_id, item.document_id, item.document_version_id)
                for item in evidence_by_identity.values()
            )
            decisions = self._enforcer.authorize_reads(context.principal, references)
        except Exception as error:
            raise RetrievalUnavailable("retrieval unavailable") from error
        authorized: list[Evidence] = []
        for identity, score in scores.items():
            evidence = evidence_by_identity[identity]
            reference = DocumentRef(
                evidence.tenant_id,
                evidence.document_id,
                evidence.document_version_id,
            )
            document = decisions.get(reference)
            if document is not None:
                authorized.append(
                    replace(
                        evidence,
                        score=score,
                        authorization_reason=document.decision.reason,
                    )
                )
        authorized.sort(
            key=lambda item: (-item.score, item.chunk_id, item.document_version_id)
        )
        strategy = "hybrid-rrf-candidate"
        if lexical is None:
            strategy += ":lexical-missing"
        elif dense is None:
            strategy += ":dense-missing"
        took_ms = sum(
            result.took_ms for result in (lexical, dense) if result is not None
        )
        limited = tuple(authorized[: context.limit])
        degradation_reason = (
            dense_failure
            if dense is None and dense_failure == "embedding_provider_unavailable"
            else None
        )
        return EvidenceSet(
            limited,
            strategy,
            len(limited),
            took_ms,
            degraded=degradation_reason is not None,
            degradation_reason=degradation_reason,
        )

    @staticmethod
    def _retrieve(
        retriever: Retriever, context: QueryContext
    ) -> tuple[EvidenceSet | None, str | None]:
        try:
            return retriever.retrieve(context), None
        except RetrievalUnavailable as error:
            return None, error.degradation_reason
