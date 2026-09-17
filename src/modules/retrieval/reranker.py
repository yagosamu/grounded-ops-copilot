"""Bounded reranking candidate with timeout fallback."""

import json
from dataclasses import dataclass, replace
from time import perf_counter
from typing import Protocol
from urllib.error import URLError
from urllib.request import Request, urlopen

from modules.retrieval.retriever import EvidenceSet


@dataclass(frozen=True)
class RerankRequest:
    query: str
    candidates: tuple[tuple[str, str], ...]
    model: str
    timeout_ms: int


@dataclass(frozen=True)
class RerankResponse:
    scores: tuple[tuple[str, float], ...]
    latency_ms: int


class RerankTimeout(TimeoutError):
    def __init__(self, latency_ms: int) -> None:
        super().__init__("reranker timeout")
        self.latency_ms = latency_ms


class RerankProvider(Protocol):
    def rerank(self, request: RerankRequest) -> RerankResponse: ...


class HTTPRerankProvider:
    """Small JSON adapter for a provider-neutral reranking endpoint."""

    def __init__(self, url: str) -> None:
        self._url = url

    def rerank(self, request: RerankRequest) -> RerankResponse:
        payload = json.dumps(
            {
                "query": request.query,
                "documents": [
                    {"id": identity, "text": text}
                    for identity, text in request.candidates
                ],
                "model": request.model,
            },
            separators=(",", ":"),
        ).encode()
        started = perf_counter()
        try:
            with urlopen(
                Request(
                    self._url,
                    data=payload,
                    headers={"content-type": "application/json"},
                    method="POST",
                ),
                timeout=request.timeout_ms / 1000,
            ) as response:
                body = json.loads(response.read())
        except TimeoutError as error:
            raise RerankTimeout(_elapsed_ms(started)) from error
        except URLError as error:
            if isinstance(error.reason, TimeoutError):
                raise RerankTimeout(_elapsed_ms(started)) from error
            raise RuntimeError("reranker unavailable") from error
        return RerankResponse(
            tuple((str(item["id"]), float(item["score"])) for item in body["results"]),
            _elapsed_ms(started),
        )


class Reranker:
    def __init__(
        self,
        provider: RerankProvider,
        model: str,
        candidate_limit: int,
        timeout_ms: int,
    ) -> None:
        if not model or candidate_limit <= 0 or timeout_ms <= 0:
            raise ValueError("invalid reranker configuration")
        self._provider = provider
        self._model = model
        self._candidate_limit = candidate_limit
        self._timeout_ms = timeout_ms

    def rerank(self, query: str, previous: EvidenceSet) -> EvidenceSet:
        if not query.strip():
            raise ValueError("invalid reranker query")
        candidates = previous.evidence[: self._candidate_limit]
        request = RerankRequest(
            query,
            tuple((evidence.chunk_id, evidence.text) for evidence in candidates),
            self._model,
            self._timeout_ms,
        )
        try:
            response = self._provider.rerank(request)
        except RerankTimeout as error:
            return EvidenceSet(
                previous.evidence,
                f"{previous.strategy}:reranker-timeout-fallback",
                previous.total,
                previous.took_ms + error.latency_ms,
            )
        expected = [evidence.chunk_id for evidence in candidates]
        returned = [identity for identity, _ in response.scores]
        if len(returned) != len(set(returned)) or set(returned) != set(expected):
            raise ValueError("invalid reranker response")
        by_identity = {evidence.chunk_id: evidence for evidence in candidates}
        previous_rank = {identity: rank for rank, identity in enumerate(expected)}
        ranked = sorted(
            response.scores,
            key=lambda item: (-item[1], previous_rank[item[0]], item[0]),
        )
        evidence = tuple(
            replace(by_identity[identity], score=score) for identity, score in ranked
        )
        return EvidenceSet(
            evidence,
            f"{previous.strategy}+reranker-candidate",
            len(evidence),
            previous.took_ms + response.latency_ms,
        )


def _elapsed_ms(started: float) -> int:
    return max(0, round((perf_counter() - started) * 1000))
