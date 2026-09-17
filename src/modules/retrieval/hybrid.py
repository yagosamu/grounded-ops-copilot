"""Deterministic reciprocal-rank fusion candidate."""

from dataclasses import replace
from typing import Protocol

from modules.policy.authorizer import Authorizer, ResourceAction
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
        authorizer: Authorizer,
        rrf_k: int = 60,
    ) -> None:
        if rrf_k <= 0:
            raise ValueError("rrf_k must be positive")
        self._lexical = lexical
        self._dense = dense
        self._authorizer = authorizer
        self._rrf_k = rrf_k

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        lexical = self._retrieve(self._lexical, context)
        dense = self._retrieve(self._dense, context)
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

        authorized: list[Evidence] = []
        for identity, score in scores.items():
            evidence = evidence_by_identity[identity]
            decision = self._authorizer.authorize(
                context.principal,
                ResourceAction(
                    evidence.tenant_id,
                    evidence.document_id,
                    "read",
                    _policy_from_reason(evidence, context),
                ),
            )
            if decision.allowed:
                authorized.append(
                    replace(evidence, score=score, authorization_reason=decision.reason)
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
        return EvidenceSet(limited, strategy, len(limited), took_ms)

    @staticmethod
    def _retrieve(retriever: Retriever, context: QueryContext) -> EvidenceSet | None:
        try:
            return retriever.retrieve(context)
        except RetrievalUnavailable:
            return None


def _policy_from_reason(evidence: Evidence, context: QueryContext) -> tuple[str, ...]:
    reason = evidence.authorization_reason
    if reason.value == "public":
        return ("public",)
    if reason.value == "principal":
        return (f"principal:{context.principal.id}",)
    if reason.value == "role":
        return tuple(f"role:{role}" for role in context.principal.roles[:1])
    if reason.value == "group":
        return tuple(f"group:{group}" for group in context.principal.groups[:1])
    return ()
