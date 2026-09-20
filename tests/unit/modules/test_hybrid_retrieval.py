"""Reciprocal-rank fusion is deterministic, deduplicated and policy safe."""

from datetime import UTC, datetime

import pytest

from adapters.policy.snapshot import SnapshotPolicyStore
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.hybrid import HybridRetriever
from modules.retrieval.retriever import (
    Evidence,
    EvidenceSet,
    QueryContext,
    RetrievalUnavailable,
)


class FrozenRetriever:
    def __init__(self, result: EvidenceSet | Exception) -> None:
        self.result = result

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def evidence(chunk: str, score: float, *, tenant: str = "alpha") -> Evidence:
    return Evidence(
        chunk,
        tenant,
        f"source-{chunk}",
        f"doc-{chunk}",
        "v1",
        0,
        f"text {chunk}",
        (0, 4),
        f"hash-{chunk}",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        score,
        AuthorizationReason.PUBLIC,
    )


CONTEXT = QueryContext("query", Principal("alice", "alpha", (), ()))


def enforcer(*items: Evidence) -> PolicyEnforcer:
    return PolicyEnforcer(
        SnapshotPolicyStore(
            tuple(
                DocumentPolicy(
                    item.tenant_id,
                    item.source_id,
                    item.document_id,
                    item.document_version_id,
                    ("public",),
                    False,
                )
                for item in items
            )
        )
    )


@pytest.mark.unit
def test_rrf_deduplicates_identity_and_combines_both_rankings() -> None:
    lexical = EvidenceSet((evidence("a", 9), evidence("b", 8)), "bm25", 2, 3)
    dense = EvidenceSet((evidence("b", 0.9), evidence("c", 0.8)), "dense", 2, 5)

    result = HybridRetriever(
        FrozenRetriever(lexical),
        FrozenRetriever(dense),
        enforcer(*lexical.evidence, *dense.evidence),
        rrf_k=60,
    ).retrieve(CONTEXT)

    assert [item.chunk_id for item in result.evidence] == ["b", "a", "c"]
    assert result.evidence[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert result.total == 3
    assert result.took_ms == 8
    assert result.strategy == "hybrid-rrf-candidate"


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["lexical", "dense"])
def test_rrf_falls_back_to_the_available_leg(missing: str) -> None:
    available = EvidenceSet((evidence("a", 1), evidence("b", 0.5)), "leg", 2, 7)
    unavailable = RetrievalUnavailable("retrieval unavailable")
    lexical = unavailable if missing == "lexical" else available
    dense = unavailable if missing == "dense" else available

    result = HybridRetriever(
        FrozenRetriever(lexical),
        FrozenRetriever(dense),
        enforcer(*available.evidence),
    ).retrieve(CONTEXT)

    assert [item.chunk_id for item in result.evidence] == ["a", "b"]
    assert result.strategy == f"hybrid-rrf-candidate:{missing}-missing"
    assert result.took_ms == 7


@pytest.mark.unit
def test_rrf_ties_use_evidence_identity_and_policy_is_rechecked() -> None:
    lexical = EvidenceSet(
        (evidence("b", 1), evidence("foreign", 0.5, tenant="beta")),
        "bm25",
        2,
        1,
    )
    dense = EvidenceSet((evidence("a", 1),), "dense", 1, 1)

    first = HybridRetriever(
        FrozenRetriever(lexical),
        FrozenRetriever(dense),
        enforcer(*lexical.evidence, *dense.evidence),
    ).retrieve(CONTEXT)
    second = HybridRetriever(
        FrozenRetriever(lexical),
        FrozenRetriever(dense),
        enforcer(*lexical.evidence, *dense.evidence),
    ).retrieve(CONTEXT)

    assert [item.chunk_id for item in first.evidence] == ["a", "b"]
    assert second == first


@pytest.mark.unit
def test_rrf_fails_when_both_legs_are_unavailable() -> None:
    failure = RetrievalUnavailable("retrieval unavailable")
    retriever = HybridRetriever(
        FrozenRetriever(failure), FrozenRetriever(failure), enforcer()
    )

    with pytest.raises(RetrievalUnavailable, match="retrieval unavailable"):
        retriever.retrieve(CONTEXT)
