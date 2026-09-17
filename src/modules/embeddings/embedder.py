"""Generate versioned query and passage embeddings behind a provider seam."""

from dataclasses import dataclass
from hashlib import sha256
from time import sleep
from typing import Protocol

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    OpenAI,
    RateLimitError,
)


@dataclass(frozen=True)
class EmbeddingRequest:
    inputs: tuple[str, ...]
    model: str
    dimensions: int


@dataclass(frozen=True)
class EmbeddingResponse:
    vectors: tuple[tuple[float, ...], ...]
    model: str
    usage_tokens: int


class EmbeddingProviderError(RuntimeError):
    """A classified provider failure safe to expose to the application."""

    def __init__(self, message: str, *, retryable: bool) -> None:
        super().__init__(message)
        self.retryable = retryable


class EmbeddingProvider(Protocol):
    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse: ...


@dataclass(frozen=True)
class EmbeddingRecord:
    vector: tuple[float, ...]
    model: str
    model_version: str
    dimensions: int
    content_hash: str
    kind: str
    usage_tokens: int


class OpenAIEmbeddingProvider:
    """OpenAI SDK adapter with retries delegated to the bounded caller."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client = OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=0,
        )

    def embed(self, request: EmbeddingRequest) -> EmbeddingResponse:
        try:
            result = self._client.embeddings.create(
                input=list(request.inputs),
                model=request.model,
                dimensions=request.dimensions,
                encoding_format="float",
            )
        except (RateLimitError, APITimeoutError, APIConnectionError) as error:
            raise EmbeddingProviderError(
                "embedding provider unavailable", retryable=True
            ) from error
        except APIStatusError as error:
            raise EmbeddingProviderError(
                "embedding provider unavailable", retryable=error.status_code >= 500
            ) from error
        vectors = tuple(
            tuple(float(value) for value in item.embedding)
            for item in sorted(result.data, key=lambda item: item.index)
        )
        return EmbeddingResponse(vectors, result.model, result.usage.total_tokens)


class Embedder:
    def __init__(
        self,
        provider: EmbeddingProvider,
        model: str,
        model_version: str,
        dimensions: int,
        *,
        batch_size: int = 64,
        max_attempts: int = 2,
        retry_delays: tuple[float, ...] = (0.05,),
    ) -> None:
        if not model or not model_version or dimensions <= 0:
            raise ValueError("invalid embedding configuration")
        if batch_size <= 0 or max_attempts <= 0:
            raise ValueError("invalid embedding bounds")
        if len(retry_delays) < max_attempts - 1 or any(
            delay < 0 for delay in retry_delays
        ):
            raise ValueError("invalid retry policy")
        self._provider = provider
        self._model = model
        self._model_version = model_version
        self._dimensions = dimensions
        self._batch_size = batch_size
        self._max_attempts = max_attempts
        self._retry_delays = retry_delays

    def embed_query(self, query: str) -> EmbeddingRecord:
        return self._embed((query,), "query")[0]

    def embed_passages(self, passages: tuple[str, ...]) -> tuple[EmbeddingRecord, ...]:
        return self._embed(passages, "passage")

    def _embed(self, inputs: tuple[str, ...], kind: str) -> tuple[EmbeddingRecord, ...]:
        self._validate_inputs(inputs)
        records: list[EmbeddingRecord] = []
        for start in range(0, len(inputs), self._batch_size):
            batch = inputs[start : start + self._batch_size]
            response = self._request(
                EmbeddingRequest(batch, self._model, self._dimensions)
            )
            if response.model != self._model or len(response.vectors) != len(batch):
                raise EmbeddingProviderError(
                    "embedding provider returned invalid response", retryable=False
                )
            if any(len(vector) != self._dimensions for vector in response.vectors):
                raise EmbeddingProviderError(
                    "embedding provider returned invalid response", retryable=False
                )
            token_shares = _distribute(response.usage_tokens, len(batch))
            records.extend(
                EmbeddingRecord(
                    vector,
                    self._model,
                    self._model_version,
                    self._dimensions,
                    sha256(content.encode()).hexdigest(),
                    kind,
                    usage,
                )
                for content, vector, usage in zip(
                    batch, response.vectors, token_shares, strict=True
                )
            )
        return tuple(records)

    def _request(self, request: EmbeddingRequest) -> EmbeddingResponse:
        for attempt in range(self._max_attempts):
            try:
                return self._provider.embed(request)
            except EmbeddingProviderError as error:
                if not error.retryable or attempt + 1 == self._max_attempts:
                    raise EmbeddingProviderError(
                        "embedding provider unavailable", retryable=error.retryable
                    ) from error
                sleep(self._retry_delays[attempt])
        raise AssertionError("unreachable")

    @staticmethod
    def _validate_inputs(inputs: tuple[str, ...]) -> None:
        token_counts = tuple(len(value.split()) for value in inputs)
        if (
            not inputs
            or any(not value.strip() for value in inputs)
            or any(count > 8192 for count in token_counts)
            or sum(token_counts) > 300_000
        ):
            raise ValueError("invalid embedding input")


def _distribute(total: int, size: int) -> tuple[int, ...]:
    quotient, remainder = divmod(total, size)
    return tuple(quotient + (index < remainder) for index in range(size))
