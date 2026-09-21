"""Failure injection across the production dependency boundaries."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from botocore.exceptions import EndpointConnectionError

from adapters.object_store.document_store import ArtifactError, DocumentStore
from adapters.policy.snapshot import SnapshotPolicyStore
from domain.answering import Question
from interfaces.http.ask import AskService
from modules.answering.abstention import AbstentionDecider
from modules.answering.context_packer import ContextPacker
from modules.answering.generator import (
    GenerationFailure,
    GenerationProviderError,
    GenerationRequest,
)
from modules.answering.verifier import CitationVerifier
from modules.embeddings.embedder import (
    Embedder,
    EmbeddingProviderError,
    EmbeddingRequest,
    EmbeddingResponse,
)
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.resilience.policies import CircuitBreaker
from modules.retrieval.dense import DenseRetriever
from modules.retrieval.hybrid import HybridRetriever
from modules.retrieval.retriever import Evidence, EvidenceSet, QueryContext
from observability.telemetry import (
    Telemetry,
    TelemetryComponent,
    TelemetryOperation,
)

pytestmark = pytest.mark.integration


def evidence() -> Evidence:
    return Evidence(
        "chunk-1",
        "alpha",
        "source-1",
        "document-1",
        "version-1",
        0,
        "Authorized operational evidence.",
        (0, 32),
        "hash-1",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 21, tzinfo=UTC),
        True,
        1.0,
        AuthorizationReason.PUBLIC,
    )


def enforcer() -> PolicyEnforcer:
    return PolicyEnforcer(
        SnapshotPolicyStore(
            (
                DocumentPolicy(
                    "alpha",
                    "source-1",
                    "document-1",
                    "version-1",
                    ("public",),
                    False,
                ),
            )
        )
    )


class FrozenRetriever:
    def __init__(self, result: EvidenceSet) -> None:
        self._result = result

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        return self._result


class FailingEmbeddingProvider:
    def __init__(self) -> None:
        self.calls = 0

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.calls += 1
        raise EmbeddingProviderError("private detail", retryable=True)


class UnusedDenseAdapter:
    calls = 0

    def search(self, request: object) -> object:
        self.calls += 1
        raise AssertionError("dense search must not run without an embedding")


def test_embedding_outage_falls_back_to_marked_bm25_without_retry_storm() -> None:
    item = evidence()
    lexical = EvidenceSet((item,), "bm25", 1, 2)
    provider = FailingEmbeddingProvider()
    dense_adapter = UnusedDenseAdapter()
    breaker = CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30)
    embedder = Embedder(
        provider,
        "embedding-model",
        "v1",
        2,
        max_attempts=2,
        retry_delays=(0.0,),
        circuit_breaker=breaker,
    )
    hybrid = HybridRetriever(
        FrozenRetriever(lexical),
        DenseRetriever(dense_adapter, embedder, enforcer(), "corpus-v1"),
        enforcer(),
    )

    result = hybrid.retrieve(
        QueryContext("where is the runbook?", Principal("alice", "alpha", (), ()))
    )

    assert [found.chunk_id for found in result.evidence] == ["chunk-1"]
    assert result.degraded is True
    assert result.degradation_reason == "embedding_provider_unavailable"
    assert result.strategy == "hybrid-rrf-candidate:dense-missing"
    assert provider.calls == 1
    assert dense_adapter.calls == 0


class FailingGenerationProvider:
    def __init__(self) -> None:
        self.calls = 0

    def stream(self, request: GenerationRequest) -> tuple[()]:
        self.calls += 1
        raise GenerationProviderError(GenerationFailure.TIMEOUT, retryable=True)


def test_generation_outage_returns_evidence_and_then_fails_fast() -> None:
    item = evidence()
    evidence_set = EvidenceSet((item,), "bm25", 1, 2)
    provider = FailingGenerationProvider()
    service = AskService(
        FrozenRetriever(evidence_set),
        ContextPacker(max_tokens=100),
        provider,
        CitationVerifier(enforcer()),
        AbstentionDecider(),
        max_generation_attempts=2,
        retry_delays=(0.0,),
    )
    principal = Principal("alice", "alpha", (), ())

    first = [
        event.payload for event in service.start(Question("q-1", "why?"), principal)
    ]
    second = [
        event.payload
        for event in service.start(Question("q-2", "why again?"), principal)
    ]

    assert first[-1]["status"] == "degraded"
    assert first[-1]["sources"] == [
        {
            "evidence_id": "chunk-1",
            "source_id": "source-1",
            "document_id": "document-1",
            "document_version_id": "version-1",
            "span": [0, 32],
        }
    ]
    assert first[-1]["degraded"] == {
        "active": True,
        "mode": "evidence_only",
        "reason": "generation_timeout",
    }
    assert second[-1]["degraded"] == {
        "active": True,
        "mode": "evidence_only",
        "reason": "generation_provider_unavailable",
    }
    assert provider.calls == 2


class FailingS3Client:
    def __init__(self, *, total_attempts: int = 1) -> None:
        self.put_calls = 0
        self.meta = SimpleNamespace(
            config=SimpleNamespace(
                connect_timeout=2.0,
                read_timeout=5.0,
                retries={"total_max_attempts": total_attempts},
            )
        )

    def put_object(self, **kwargs: object) -> None:
        self.put_calls += 1
        raise EndpointConnectionError(endpoint_url="http://object-store")


def test_object_store_outage_is_redacted_and_fails_fast_after_threshold() -> None:
    client = FailingS3Client()
    store = DocumentStore(
        client,
        "artifacts",
        CircuitBreaker(failure_threshold=1, recovery_timeout_seconds=30),
    )

    for _ in range(2):
        with pytest.raises(ArtifactError, match="artifact unavailable") as caught:
            store.put("alpha", "raw", b"private corpus")
        assert "object-store" not in str(caught.value)
        assert "private corpus" not in str(caught.value)

    assert client.put_calls == 1


def test_object_store_rejects_transport_retry_multiplication() -> None:
    with pytest.raises(ValueError, match="dependency attempt bound"):
        DocumentStore(FailingS3Client(total_attempts=2), "artifacts")


class FailingLogger(logging.Logger):
    def log(
        self,
        level: int,
        msg: object,
        *args: object,
        **kwargs: object,
    ) -> None:
        raise RuntimeError("telemetry unavailable")


def test_telemetry_outage_does_not_replace_the_business_result() -> None:
    telemetry = Telemetry(logger=FailingLogger("unavailable"))
    result = ""

    with telemetry.operation(
        TelemetryComponent.RETRIEVAL,
        TelemetryOperation.RETRIEVAL_QUERY,
    ):
        result = "authorized evidence"

    assert result == "authorized evidence"
