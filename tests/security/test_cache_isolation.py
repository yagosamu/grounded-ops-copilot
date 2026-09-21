"""Adversarial cache replay cannot cross authorization boundaries."""

from datetime import UTC, datetime

import pytest

from modules.cache.cache_policy import CacheVersions, VersionSafeCache
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.retrieval.retriever import Evidence, EvidenceSet, QueryContext

pytestmark = pytest.mark.security


class ReplayingBackend:
    def __init__(self) -> None:
        self.record: object | None = None

    def get(self, key: str) -> object | None:
        return self.record

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        self.record = value


def result(principal_id: str) -> EvidenceSet:
    return EvidenceSet(
        (
            Evidence(
                f"chunk-{principal_id}",
                "alpha",
                "source-1",
                f"private-{principal_id}",
                "version-1",
                0,
                f"Private evidence for {principal_id}.",
                (0, 20),
                f"hash-{principal_id}",
                "markdown-v1",
                "structural-v1",
                datetime(2026, 9, 21, tzinfo=UTC),
                True,
                1.0,
                AuthorizationReason.PRINCIPAL,
            ),
        ),
        "bm25",
        1,
        2,
    )


def test_replayed_record_for_another_principal_is_rejected() -> None:
    backend = ReplayingBackend()
    cache = VersionSafeCache(backend)
    versions = CacheVersions("policy-v1", "corpus-v1", "model-v1")
    alice = Principal("alice", "alpha", (), ())
    bob = Principal("bob", "alpha", (), ())

    alice_result = cache.retrieval(
        QueryContext("private procedure", alice), versions, lambda: result("alice")
    )
    bob_result = cache.retrieval(
        QueryContext("private procedure", bob), versions, lambda: result("bob")
    )

    assert alice_result.value.evidence[0].document_id == "private-alice"
    assert bob_result.cache_hit is False
    assert bob_result.value.evidence[0].document_id == "private-bob"
    assert "alice" not in bob_result.value.evidence[0].text
