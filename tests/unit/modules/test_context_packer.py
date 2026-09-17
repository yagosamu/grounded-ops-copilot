"""Bounded context keeps diverse, versioned evidence deterministic."""

from datetime import UTC, datetime

import pytest

from modules.answering.context_packer import ContextPacker
from modules.policy.authorizer import AuthorizationReason
from modules.retrieval.retriever import Evidence, EvidenceSet


def evidence(
    chunk_id: str,
    source_id: str,
    text: str,
    *,
    document_id: str | None = None,
    version_id: str = "version-1",
    current: bool = True,
) -> Evidence:
    return Evidence(
        chunk_id,
        "alpha",
        source_id,
        document_id or f"document-{source_id}",
        version_id,
        0,
        text,
        (5, 5 + len(text)),
        f"hash-{chunk_id}",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        current,
        4.0,
        AuthorizationReason.PUBLIC,
    )


@pytest.mark.unit
def test_packs_diverse_evidence_with_a_hard_token_bound_and_provenance() -> None:
    first = evidence("chunk-a1", "source-a", "one two three")
    same_source = evidence("chunk-a2", "source-a", "four five")
    other_source = evidence("chunk-b1", "source-b", "six seven")
    too_large = evidence("chunk-c1", "source-c", "eight nine ten eleven")
    result = ContextPacker(max_tokens=7).pack(
        EvidenceSet((first, same_source, other_source, too_large), "bm25", 4, 8)
    )

    assert result.evidence == (first, other_source, same_source)
    assert result.token_count == 7
    assert result.max_tokens == 7
    assert result.evidence[0].document_version_id == "version-1"
    assert result.evidence[0].span == (5, 18)
    assert result.evidence[0].content_hash == "hash-chunk-a1"
    assert result.truncated is True


@pytest.mark.unit
def test_deduplicates_only_the_same_versioned_evidence_identity() -> None:
    original = evidence("chunk-1", "source-a", "one two")
    duplicate = evidence("chunk-1", "source-a", "one two")
    historical = evidence(
        "chunk-1",
        "source-a",
        "old words",
        document_id="document-a",
        version_id="version-0",
        current=False,
    )

    result = ContextPacker(max_tokens=10).pack(
        EvidenceSet((original, duplicate, historical), "bm25", 3, 4)
    )

    assert result.evidence == (original, historical)
    assert result.token_count == 4
    assert result.truncated is False


@pytest.mark.unit
def test_preserves_and_reports_conflicting_document_versions() -> None:
    current = evidence(
        "chunk-current",
        "source-a",
        "use the current setting",
        document_id="document-1",
        version_id="version-2",
    )
    historical = evidence(
        "chunk-old",
        "source-a",
        "use the old setting",
        document_id="document-1",
        version_id="version-1",
        current=False,
    )

    result = ContextPacker(max_tokens=20).pack(
        EvidenceSet((current, historical), "bm25", 2, 3)
    )

    assert result.evidence == (current, historical)
    assert result.conflicts == (("document-1", ("version-1", "version-2")),)


@pytest.mark.unit
def test_empty_context_is_stable() -> None:
    packer = ContextPacker(max_tokens=5)
    evidence_set = EvidenceSet((), "bm25", 0, 2)

    assert packer.pack(evidence_set) == packer.pack(evidence_set)
    assert packer.pack(evidence_set).evidence == ()
    assert packer.pack(evidence_set).token_count == 0
    assert packer.pack(evidence_set).conflicts == ()
    assert packer.pack(evidence_set).truncated is False


@pytest.mark.unit
def test_never_exceeds_any_positive_budget() -> None:
    items = tuple(
        evidence(f"chunk-{index}", f"source-{index % 3}", "word " * (index + 1))
        for index in range(8)
    )

    for budget in range(1, 16):
        result = ContextPacker(max_tokens=budget).pack(
            EvidenceSet(items, "bm25", len(items), 1)
        )
        assert result.token_count <= budget
        assert sum(len(item.text.split()) for item in result.evidence) == (
            result.token_count
        )


@pytest.mark.unit
def test_rejects_non_positive_token_budget() -> None:
    with pytest.raises(ValueError, match="invalid context token budget"):
        ContextPacker(max_tokens=0)
