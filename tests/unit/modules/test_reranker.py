"""Reranking is bounded, provenance preserving and timeout safe."""

from datetime import UTC, datetime

import pytest

from modules.policy.authorizer import AuthorizationReason
from modules.retrieval.reranker import (
    Reranker,
    RerankRequest,
    RerankResponse,
    RerankTimeout,
)
from modules.retrieval.retriever import Evidence, EvidenceSet


class FrozenProvider:
    def __init__(self, outcome: RerankResponse | RerankTimeout) -> None:
        self.outcome = outcome
        self.requests: list[RerankRequest] = []

    def rerank(self, request: RerankRequest) -> RerankResponse:
        self.requests.append(request)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


def item(identity: str, score: float) -> Evidence:
    return Evidence(
        identity,
        "alpha",
        f"source-{identity}",
        f"doc-{identity}",
        "v1",
        0,
        f"evidence {identity}",
        (2, 12),
        f"hash-{identity}",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        score,
        AuthorizationReason.PUBLIC,
    )


@pytest.mark.unit
def test_reranks_only_the_bounded_set_and_preserves_evidence_identity() -> None:
    original = EvidenceSet(
        (item("a", 0.9), item("b", 0.8), item("c", 0.7)), "hybrid", 3, 20
    )
    provider = FrozenProvider(RerankResponse((("b", 0.99), ("a", 0.1)), 125))

    result = Reranker(
        provider, model="reranker-v1", candidate_limit=2, timeout_ms=400
    ).rerank("query", original)

    assert provider.requests == [
        RerankRequest(
            "query",
            (("a", "evidence a"), ("b", "evidence b")),
            "reranker-v1",
            400,
        )
    ]
    assert [e.chunk_id for e in result.evidence] == ["b", "a"]
    assert result.evidence[0].score == 0.99
    assert result.evidence[0].content_hash == "hash-b"
    assert result.evidence[0].span == (2, 12)
    assert result.evidence[0].authorization_reason is AuthorizationReason.PUBLIC
    assert result.strategy == "hybrid+reranker-candidate"
    assert result.took_ms == 145


@pytest.mark.unit
def test_timeout_returns_the_exact_previous_ranking_and_accounts_latency() -> None:
    original = EvidenceSet((item("a", 0.9), item("b", 0.8)), "hybrid", 2, 20)
    provider = FrozenProvider(RerankTimeout(400))

    result = Reranker(provider, "reranker-v1", 10, 400).rerank("query", original)

    assert result.evidence == original.evidence
    assert result.strategy == "hybrid:reranker-timeout-fallback"
    assert result.took_ms == 420


@pytest.mark.unit
def test_rejects_missing_or_duplicate_provider_identities() -> None:
    original = EvidenceSet((item("a", 0.9), item("b", 0.8)), "hybrid", 2, 20)

    for scores in [(("a", 1.0),), (("a", 1.0), ("a", 0.5))]:
        provider = FrozenProvider(RerankResponse(scores, 5))
        with pytest.raises(ValueError, match="invalid reranker response"):
            Reranker(provider, "reranker-v1", 2, 400).rerank("query", original)
