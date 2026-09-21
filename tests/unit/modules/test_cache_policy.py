"""Versioned cache keys preserve authorization and freshness boundaries."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from domain.answering import (
    AbstentionReason,
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

pytestmark = pytest.mark.unit


class MemoryBackend:
    def __init__(self) -> None:
        self.records: dict[str, object] = {}
        self.writes = 0

    def get(self, key: str) -> object | None:
        return self.records.get(key)

    def set(self, key: str, value: object, ttl_seconds: int) -> None:
        self.records[key] = value
        self.writes += 1


def principal(
    principal_id: str = "alice",
    tenant_id: str = "alpha",
    roles: tuple[str, ...] = ("operator",),
) -> Principal:
    return Principal(principal_id, tenant_id, roles, ("platform",))


def versions(
    *,
    policy: str = "policy-v1",
    corpus: str = "corpus-v1",
    model: str = "model-v1",
) -> CacheVersions:
    return CacheVersions(policy, corpus, model)


def evidence_set(*, tenant_id: str = "alpha", degraded: bool = False) -> EvidenceSet:
    return EvidenceSet(
        (
            Evidence(
                "chunk-1",
                tenant_id,
                "source-1",
                "document-1",
                "version-1",
                0,
                "Authorized evidence.",
                (0, 20),
                "hash-1",
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
        degraded=degraded,
        degradation_reason="embedding_provider_unavailable" if degraded else None,
    )


def verified_answer(question: Question) -> GroundedAnswer:
    return GroundedAnswer(
        question,
        (
            Claim(
                "Authorized evidence.",
                (Citation("chunk-1", "version-1", (0, 20), resolved=True),),
            ),
        ),
        VerificationStatus.VERIFIED,
        AnswerUsage("model-v1", 10, 4),
    )


def test_retrieval_key_changes_across_every_freshness_and_access_boundary() -> None:
    cache = VersionSafeCache(MemoryBackend())
    base_context = QueryContext("recovery procedure", principal())
    base = cache.retrieval_key(base_context, versions())

    keys = {
        base,
        cache.retrieval_key(
            QueryContext("recovery procedure", principal(tenant_id="beta")), versions()
        ),
        cache.retrieval_key(
            QueryContext("recovery procedure", principal(principal_id="bob")),
            versions(),
        ),
        cache.retrieval_key(
            QueryContext("recovery procedure", principal(roles=("viewer",))),
            versions(),
        ),
        cache.retrieval_key(base_context, versions(policy="policy-v2")),
        cache.retrieval_key(base_context, versions(corpus="corpus-v2")),
        cache.retrieval_key(base_context, versions(model="model-v2")),
        cache.retrieval_key(replace(base_context, query="different query"), versions()),
        cache.retrieval_key(replace(base_context, limit=20), versions()),
        cache.retrieval_key(
            replace(base_context, document_ids=("document-1",)), versions()
        ),
        cache.retrieval_key(replace(base_context, include_historical=True), versions()),
    }

    assert len(keys) == 11
    assert base.startswith("groundedops:v1:retrieval:")
    assert "alice" not in base
    assert "alpha" not in base


def test_answer_key_is_separate_and_changes_with_question_and_versions() -> None:
    cache = VersionSafeCache(MemoryBackend())
    first = Question("question-1", "What is the procedure?")
    second = Question("question-2", "What changed?")

    keys = {
        cache.answer_key(first, principal(), versions()),
        cache.answer_key(second, principal(), versions()),
        cache.answer_key(first, principal(), versions(policy="policy-v2")),
        cache.answer_key(first, principal(), versions(corpus="corpus-v2")),
        cache.answer_key(first, principal(), versions(model="model-v2")),
    }

    assert len(keys) == 5
    assert all(key.startswith("groundedops:v1:answer:") for key in keys)


def test_only_authorized_non_degraded_retrieval_results_are_cached() -> None:
    backend = MemoryBackend()
    cache = VersionSafeCache(backend)
    context = QueryContext("procedure", principal())

    degraded = cache.retrieval(context, versions(), lambda: evidence_set(degraded=True))
    wrong_tenant = cache.retrieval(
        context,
        versions(corpus="corpus-v2"),
        lambda: evidence_set(tenant_id="beta"),
    )

    assert degraded.cache_hit is False
    assert degraded.value.degraded is True
    assert wrong_tenant.cache_hit is False
    assert wrong_tenant.value.evidence[0].tenant_id == "beta"
    assert backend.writes == 0


def test_only_verified_answers_are_cached() -> None:
    backend = MemoryBackend()
    cache = VersionSafeCache(backend)
    question = Question("question-1", "What is the procedure?")
    abstained = GroundedAnswer(
        question,
        (),
        VerificationStatus.ABSTAINED,
        AnswerUsage("none", 0, 0),
        AbstentionReason.INSUFFICIENT_EVIDENCE,
    )

    result = cache.answer(question, principal(), versions(), lambda: abstained)

    assert result.cache_hit is False
    assert result.value.status is VerificationStatus.ABSTAINED
    assert backend.writes == 0


def test_unknown_principals_never_populate_the_cache() -> None:
    backend = MemoryBackend()
    cache = VersionSafeCache(backend)
    unknown = Principal("unknown", "alpha", (), (), known=False)
    context = QueryContext("procedure", unknown)
    question = Question("question-1", "What is the procedure?")

    retrieval = cache.retrieval(context, versions(), evidence_set)
    generated = cache.answer(
        question,
        unknown,
        versions(),
        lambda: verified_answer(question),
    )

    assert retrieval.cache_hit is False
    assert generated.cache_hit is False
    assert backend.writes == 0


@pytest.mark.parametrize("value", ["", " "])
def test_rejects_missing_version_boundaries(value: str) -> None:
    with pytest.raises(ValueError, match="invalid cache version"):
        CacheVersions(value, "corpus-v1", "model-v1")
