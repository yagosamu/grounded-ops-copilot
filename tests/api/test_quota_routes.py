"""HTTP quota enforcement returns stable public failures."""

from hashlib import sha256

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interfaces.http.search import create_search_router
from modules.policy.quotas import (
    DEFAULT_QUOTA_POLICY,
    InMemoryQuotaBackend,
    QuotaBackendError,
    QuotaEnforcer,
    QuotaLimits,
    QuotaOperation,
)
from modules.retrieval.retriever import EvidenceSet

pytestmark = pytest.mark.api
KEY = sha256(b"api-quota-test-key").digest()


class FakeRetriever:
    def retrieve(self, context: object) -> EvidenceSet:
        return EvidenceSet((), "bm25", 0, 1)


class FailingBackend(InMemoryQuotaBackend):
    def reserve(self, *args: object, **kwargs: object) -> object:
        raise QuotaBackendError("private quota provider")


def client(auth_context, quotas: QuotaEnforcer) -> TestClient:
    app = FastAPI()
    app.include_router(
        create_search_router(FakeRetriever(), auth_context.authenticator, quotas=quotas)
    )
    return TestClient(app)


def quota(backend: InMemoryQuotaBackend) -> QuotaEnforcer:
    policy = dict(DEFAULT_QUOTA_POLICY)
    policy[QuotaOperation.SEARCH] = QuotaLimits(1, 60, 8)
    return QuotaEnforcer(backend, policy, KEY)


def test_search_returns_429_and_retry_after_after_the_burst_is_exhausted(
    auth_context,
) -> None:
    api = client(auth_context, quota(InMemoryQuotaBackend()))
    headers = auth_context.headers()

    first = api.get("/v1/evidence/search", params={"q": "tracer"}, headers=headers)
    second = api.get("/v1/evidence/search", params={"q": "tracer"}, headers=headers)

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"detail": "quota exceeded"}
    assert 1 <= int(second.headers["retry-after"]) <= 60


def test_search_fails_closed_when_the_quota_backend_is_unavailable(
    auth_context,
) -> None:
    api = client(auth_context, quota(FailingBackend()))

    response = api.get(
        "/v1/evidence/search",
        params={"q": "tracer"},
        headers=auth_context.headers(),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "quota temporarily unavailable"}
    assert "private" not in response.text
