"""The evidence route exposes only authenticated, policy-filtered results."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interfaces.http.search import create_search_router
from modules.policy.authorizer import AuthorizationReason
from modules.retrieval.retriever import (
    Evidence,
    EvidenceSet,
    QueryContext,
    RetrievalUnavailable,
)


class FakeRetriever:
    def __init__(self, result: EvidenceSet | Exception) -> None:
        self.result = result
        self.context: QueryContext | None = None

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        self.context = context
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def client(retriever: FakeRetriever) -> TestClient:
    app = FastAPI()
    app.include_router(create_search_router(retriever))
    return TestClient(app)


def evidence_set() -> EvidenceSet:
    return EvidenceSet(
        (
            Evidence(
                "chunk-1",
                "alpha",
                "source-1",
                "document-1",
                "version-2",
                0,
                "TracerProvider provides access",
                (5, 35),
                "hash-1",
                "markdown-v1",
                "structural-v1",
                datetime(2026, 9, 17, tzinfo=UTC),
                True,
                4.2,
                AuthorizationReason.ROLE,
            ),
        ),
        "bm25",
        1,
        12,
    )


HEADERS = {
    "X-Principal-Id": "alice",
    "X-Tenant-Id": "alpha",
    "X-Roles": "engineer,reader",
    "X-Groups": "oncall",
}


@pytest.mark.api
def test_returns_authorized_evidence_and_diagnostics() -> None:
    response = client(FakeRetriever(evidence_set())).get(
        "/v1/evidence/search", params={"q": "tracer provider"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {
        "evidence": [
            {
                "chunk_id": "chunk-1",
                "tenant_id": "alpha",
                "source_id": "source-1",
                "document_id": "document-1",
                "document_version_id": "version-2",
                "ordinal": 0,
                "text": "TracerProvider provides access",
                "span": [5, 35],
                "content_hash": "hash-1",
                "parser_version": "markdown-v1",
                "chunker_version": "structural-v1",
                "source_timestamp": "2026-09-17T00:00:00Z",
                "is_current": True,
                "score": 4.2,
                "authorization_reason": "role",
            }
        ],
        "diagnostics": {"strategy": "bm25", "total": 1, "took_ms": 12},
    }


@pytest.mark.api
def test_validates_query_and_pagination_bounds() -> None:
    api = client(FakeRetriever(evidence_set()))

    assert api.get("/v1/evidence/search", headers=HEADERS).status_code == 422
    assert (
        api.get(
            "/v1/evidence/search", params={"q": "valid", "limit": 101}, headers=HEADERS
        ).status_code
        == 422
    )
    assert (
        api.get(
            "/v1/evidence/search", params={"q": "valid", "offset": -1}, headers=HEADERS
        ).status_code
        == 422
    )


@pytest.mark.api
def test_passes_pagination_and_filters_to_retrieval() -> None:
    retriever = FakeRetriever(evidence_set())

    response = client(retriever).get(
        "/v1/evidence/search",
        params={
            "q": "tracer",
            "limit": 5,
            "offset": 10,
            "source_id": "otel",
            "document_id": "doc-1",
            "include_historical": True,
        },
        headers=HEADERS,
    )

    assert response.status_code == 200
    assert retriever.context is not None
    assert retriever.context.limit == 5
    assert retriever.context.offset == 10
    assert retriever.context.source_ids == ("otel",)
    assert retriever.context.document_ids == ("doc-1",)
    assert retriever.context.include_historical is True
    assert retriever.context.principal.roles == ("engineer", "reader")
    assert retriever.context.principal.groups == ("oncall",)


@pytest.mark.api
def test_rejects_missing_or_unknown_principal() -> None:
    api = client(FakeRetriever(evidence_set()))

    missing = api.get("/v1/evidence/search", params={"q": "tracer"})
    unknown = api.get(
        "/v1/evidence/search",
        params={"q": "tracer"},
        headers=HEADERS | {"X-Principal-Known": "false"},
    )

    assert missing.status_code == 401
    assert missing.json() == {"detail": "authentication required"}
    assert unknown.status_code == 403
    assert unknown.json() == {"detail": "principal unauthorized"}


@pytest.mark.api
def test_returns_empty_success() -> None:
    response = client(FakeRetriever(EvidenceSet((), "bm25", 0, 4))).get(
        "/v1/evidence/search", params={"q": "missing"}, headers=HEADERS
    )

    assert response.status_code == 200
    assert response.json() == {
        "evidence": [],
        "diagnostics": {"strategy": "bm25", "total": 0, "took_ms": 4},
    }


@pytest.mark.api
def test_returns_redacted_dependency_failure() -> None:
    response = client(FakeRetriever(RetrievalUnavailable("private endpoint"))).get(
        "/v1/evidence/search", params={"q": "tracer"}, headers=HEADERS
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "retrieval temporarily unavailable"}
    assert "private" not in response.text
