"""Versioned embedding generation is bounded and deterministic."""

import pytest

from modules.embeddings.embedder import (
    Embedder,
    EmbeddingProviderError,
    EmbeddingRequest,
    EmbeddingResponse,
)


class FrozenProvider:
    def __init__(
        self, outcomes: list[EmbeddingResponse | EmbeddingProviderError]
    ) -> None:
        self.outcomes = outcomes
        self.requests: list[EmbeddingRequest] = []

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def response(*vectors: tuple[float, ...]) -> EmbeddingResponse:
    return EmbeddingResponse(vectors, "text-embedding-3-small", 9)


@pytest.mark.unit
def test_batches_embeddings_with_versioned_content_metadata() -> None:
    provider = FrozenProvider([response((1.0, 0.0), (0.0, 1.0)), response((0.5, 0.5))])
    embedder = Embedder(
        provider,
        model="text-embedding-3-small",
        model_version="2026-09-01",
        dimensions=2,
        batch_size=2,
        max_attempts=2,
    )

    records = embedder.embed_passages(("alpha", "beta", "gamma"))

    assert [request.inputs for request in provider.requests] == [
        ("alpha", "beta"),
        ("gamma",),
    ]
    assert tuple(record.vector for record in records) == (
        (1.0, 0.0),
        (0.0, 1.0),
        (0.5, 0.5),
    )
    assert records[0].model == "text-embedding-3-small"
    assert records[0].model_version == "2026-09-01"
    assert records[0].dimensions == 2
    assert records[0].content_hash == (
        "8ed3f6ad685b959ead7022518e1af76cd816f8e8ec7ccdda1ed4018e8f2223f8"
    )
    assert records[0].kind == "passage"
    assert sum(record.usage_tokens for record in records) == 18


@pytest.mark.unit
def test_query_embedding_uses_the_same_versioned_contract() -> None:
    provider = FrozenProvider([response((0.25, 0.75))])
    embedder = Embedder(provider, "text-embedding-3-small", "v1", 2)

    record = embedder.embed_query("where is the trace resource?")

    assert record.vector == (0.25, 0.75)
    assert record.kind == "query"
    assert provider.requests == [
        EmbeddingRequest(("where is the trace resource?",), "text-embedding-3-small", 2)
    ]


@pytest.mark.unit
def test_retries_only_retryable_failures_with_a_bounded_attempt_count() -> None:
    provider = FrozenProvider(
        [
            EmbeddingProviderError("rate limited", retryable=True),
            EmbeddingProviderError("temporary", retryable=True),
            response((1.0, 0.0)),
        ]
    )
    embedder = Embedder(
        provider,
        "text-embedding-3-small",
        "v1",
        2,
        max_attempts=3,
        retry_delays=(0.0, 0.0),
    )

    assert embedder.embed_query("retry me").vector == (1.0, 0.0)
    assert len(provider.requests) == 3


@pytest.mark.unit
@pytest.mark.parametrize("retryable", [False, True])
def test_provider_failure_is_safe_and_never_retries_beyond_the_bound(
    retryable: bool,
) -> None:
    failures: list[EmbeddingResponse | EmbeddingProviderError] = [
        EmbeddingProviderError("private provider detail", retryable=retryable),
        EmbeddingProviderError("private provider detail", retryable=retryable),
    ]
    provider = FrozenProvider(failures)
    embedder = Embedder(
        provider,
        "text-embedding-3-small",
        "v1",
        2,
        max_attempts=2,
        retry_delays=(0.0,),
    )

    with pytest.raises(EmbeddingProviderError, match="embedding provider unavailable"):
        embedder.embed_query("fail safely")

    assert len(provider.requests) == (2 if retryable else 1)


@pytest.mark.unit
def test_rejects_empty_or_oversized_provider_inputs() -> None:
    provider = FrozenProvider([])
    embedder = Embedder(provider, "text-embedding-3-small", "v1", 2)

    for values in [(), ("",), ("word " * 8193,)]:
        with pytest.raises(ValueError, match="invalid embedding input"):
            embedder.embed_passages(values)

    assert provider.requests == []
