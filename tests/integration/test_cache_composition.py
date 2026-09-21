"""Cache composition reuses only results valid for the current versions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from domain.answering import (
    AnswerUsage,
    Citation,
    Claim,
    GroundedAnswer,
    Question,
    VerificationStatus,
)
from modules.cache.cache_policy import CacheVersions, VersionSafeCache
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.retrieval.retriever import Evidence, EvidenceSet, QueryContext

pytestmark = pytest.mark.integration


class MemoryBackend:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}

    def get(self, key: str) -> object | None:
        return self.records.get(key)

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        self.records[key] = value


class FailingBackend:
    def get(self, key: str) -> object | None:
        raise RuntimeError("cache unavailable")

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        raise RuntimeError("cache unavailable")


def principal() -> Principal:
    return Principal("alice", "alpha", ("operator",), ("platform",))


def versions(
    policy: str = "policy-v1",
    corpus: str = "corpus-v1",
    model: str = "model-v1",
) -> CacheVersions:
    return CacheVersions(policy, corpus, model)


def evidence(document_version: str) -> EvidenceSet:
    return EvidenceSet(
        (
            Evidence(
                f"chunk-{document_version}",
                "alpha",
                "source-1",
                "document-1",
                document_version,
                0,
                f"Evidence from {document_version}.",
                (0, 20),
                f"hash-{document_version}",
                "markdown-v1",
                "structural-v1",
                datetime(2026, 9, 21, tzinfo=UTC),
                True,
                1.0,
                AuthorizationReason.ROLE,
            ),
        ),
        "bm25",
        1,
        2,
    )


def answer(question: Question, text: str) -> GroundedAnswer:
    return GroundedAnswer(
        question,
        (
            Claim(
                text,
                (Citation("chunk-version-1", "version-1", (0, 20), True),),
            ),
        ),
        VerificationStatus.VERIFIED,
        AnswerUsage("model-v1", 10, 4),
    )


def test_retrieval_hit_avoids_work_and_version_changes_invalidate_old_results() -> None:
    cache = VersionSafeCache(MemoryBackend())
    context = QueryContext("recovery", principal())
    calls = 0

    def load() -> EvidenceSet:
        nonlocal calls
        calls += 1
        return evidence(f"version-{calls}")

    first = cache.retrieval(context, versions(), load)
    hit = cache.retrieval(context, versions(), load)
    policy_change = cache.retrieval(context, versions(policy="policy-v2"), load)
    document_change = cache.retrieval(
        context, versions(policy="policy-v2", corpus="corpus-v2"), load
    )
    model_change = cache.retrieval(
        context,
        versions(policy="policy-v2", corpus="corpus-v2", model="model-v2"),
        load,
    )

    assert first.cache_hit is False
    assert hit.cache_hit is True
    assert hit.value.evidence[0].document_version_id == "version-1"
    assert policy_change.value.evidence[0].document_version_id == "version-2"
    assert document_change.value.evidence[0].document_version_id == "version-3"
    assert model_change.value.evidence[0].document_version_id == "version-4"
    assert calls == 4


def test_answer_hit_rebinds_request_identity_without_regeneration() -> None:
    cache = VersionSafeCache(MemoryBackend())
    original = Question("question-1", "What is the procedure?")
    repeated = Question("question-2", original.text)
    after_model_change = Question("question-3", original.text)
    calls = 0

    def load() -> GroundedAnswer:
        nonlocal calls
        calls += 1
        return answer(original, f"Use runbook revision {calls}.")

    first = cache.answer(original, principal(), versions(), load)
    hit = cache.answer(repeated, principal(), versions(), load)
    regenerated = cache.answer(
        after_model_change,
        principal(),
        versions(model="model-v2"),
        load,
    )

    assert first.cache_hit is False
    assert hit.cache_hit is True
    assert hit.value.question == repeated
    assert hit.value.claims[0].text == "Use runbook revision 1."
    assert regenerated.cache_hit is False
    assert regenerated.value.claims[0].text == "Use runbook revision 2."
    assert calls == 2


def test_cache_backend_failure_falls_back_to_fresh_computation() -> None:
    cache = VersionSafeCache(FailingBackend())
    context = QueryContext("recovery", principal())
    calls = 0

    def load() -> EvidenceSet:
        nonlocal calls
        calls += 1
        return evidence(f"version-{calls}")

    first = cache.retrieval(context, versions(), load)
    second = cache.retrieval(context, versions(), load)

    assert first.cache_hit is False
    assert second.cache_hit is False
    assert first.value.evidence[0].document_version_id == "version-1"
    assert second.value.evidence[0].document_version_id == "version-2"
    assert calls == 2
