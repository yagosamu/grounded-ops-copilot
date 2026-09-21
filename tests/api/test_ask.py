"""Ask streams unverified progress and only finalizes verified answers."""

import json
from collections.abc import Generator
from datetime import UTC, datetime

import anyio
import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from adapters.policy.snapshot import SnapshotPolicyStore
from domain.answering import Question
from interfaces.http.ask import AskEvent, AskService, AskSession, create_ask_router
from interfaces.http.auth import PrincipalDependency
from modules.answering.abstention import AbstentionDecider
from modules.answering.context_packer import ContextPacker
from modules.answering.generator import (
    GenerationCompleted,
    GenerationDelta,
    GenerationFailure,
    GenerationProviderError,
    GenerationRequest,
    GenerationResponse,
    ProposedCitation,
    ProposedClaim,
)
from modules.answering.verifier import CitationVerifier
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.retriever import Evidence, EvidenceSet, QueryContext


def evidence() -> Evidence:
    return Evidence(
        "chunk-1",
        "alpha",
        "source-1",
        "document-1",
        "version-2",
        0,
        "TracerProvider provides access to tracers.",
        (5, 45),
        "hash-1",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        4.2,
        AuthorizationReason.ROLE,
    )


class FakeRetriever:
    def __init__(self, result: EvidenceSet) -> None:
        self.result = result
        self.contexts: list[QueryContext] = []

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        self.contexts.append(context)
        return self.result


class FakeStreamingProvider:
    def __init__(
        self,
        outcomes: list[
            tuple[GenerationDelta | GenerationCompleted, ...] | GenerationProviderError
        ],
    ) -> None:
        self.outcomes = outcomes
        self.requests: list[GenerationRequest] = []

    def stream(
        self, request: GenerationRequest
    ) -> tuple[GenerationDelta | GenerationCompleted, ...]:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def completed() -> GenerationCompleted:
    return GenerationCompleted(
        GenerationResponse(
            (
                ProposedClaim(
                    "TracerProvider provides access to tracers.",
                    (ProposedCitation("chunk-1", "version-2", (5, 45)),),
                ),
            ),
            "gpt-test",
            12,
            7,
        )
    )


def ask_service(
    result: EvidenceSet, provider: FakeStreamingProvider
) -> tuple[AskService, FakeRetriever]:
    retriever = FakeRetriever(result)
    policies = tuple(
        DocumentPolicy(
            item.tenant_id,
            item.source_id,
            item.document_id,
            item.document_version_id,
            ("public",),
            False,
        )
        for item in result.evidence
    )
    return (
        AskService(
            retriever,
            ContextPacker(max_tokens=100),
            provider,
            CitationVerifier(PolicyEnforcer(SnapshotPolicyStore(policies))),
            AbstentionDecider(),
            max_generation_attempts=2,
            retry_delays=(0.0,),
        ),
        retriever,
    )


def client(service: AskService, authenticator: PrincipalDependency) -> TestClient:
    app = FastAPI()
    app.include_router(create_ask_router(service, authenticator))
    return TestClient(app)


def events(response_text: str) -> list[dict[str, object]]:
    return [json.loads(line) for line in response_text.splitlines()]


def reject_unknown_principal() -> Principal:
    raise HTTPException(status_code=403, detail="principal unauthorized")


@pytest.mark.api
def test_streams_unverified_delta_then_a_verified_final_answer(auth_context) -> None:
    provider = FakeStreamingProvider(
        [(GenerationDelta("TracerProvider provides "), completed())]
    )
    service, retriever = ask_service(EvidenceSet((evidence(),), "bm25", 1, 3), provider)

    response = client(service, auth_context.authenticator).post(
        "/v1/ask",
        json={"question": "What provides access?"},
        headers=auth_context.headers(),
    )
    payloads = events(response.text)

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/x-ndjson")
    assert payloads[0] == {"type": "status", "status": "retrieving"}
    assert payloads[1] == {"type": "status", "status": "generating"}
    assert payloads[2] == {
        "type": "delta",
        "status": "unverified",
        "text": "TracerProvider provides ",
    }
    final = payloads[3]
    assert final["type"] == "final"
    assert final["status"] == "verified"
    assert final["answer"] == "TracerProvider provides access to tracers."
    assert final["claims"][0]["citations"][0] == {
        "evidence_id": "chunk-1",
        "document_version_id": "version-2",
        "span": [5, 45],
        "resolved": True,
    }
    assert final["sources"] == [
        {
            "evidence_id": "chunk-1",
            "source_id": "source-1",
            "document_id": "document-1",
            "document_version_id": "version-2",
            "span": [5, 45],
        }
    ]
    assert final["usage"] == {
        "model": "gpt-test",
        "input_tokens": 12,
        "output_tokens": 7,
    }
    assert final["abstention"] is None
    assert final["degraded"] == {"active": False, "mode": None, "reason": None}
    assert retriever.contexts[0].principal.id == "alice"
    assert retriever.contexts[0].principal.tenant_id == "alpha"


@pytest.mark.api
def test_streams_abstention_without_calling_generation_for_empty_evidence(
    auth_context,
) -> None:
    provider = FakeStreamingProvider([])
    service, _ = ask_service(EvidenceSet((), "bm25", 0, 2), provider)

    response = client(service, auth_context.authenticator).post(
        "/v1/ask",
        json={"question": "What is missing?"},
        headers=auth_context.headers(),
    )
    payloads = events(response.text)

    assert response.status_code == 200
    assert [item["type"] for item in payloads] == ["status", "final"]
    assert payloads[-1]["status"] == "abstained"
    assert payloads[-1]["answer"] == (
        "I do not have enough authorized evidence to answer this question."
    )
    assert payloads[-1]["abstention"] == "insufficient_evidence"
    assert payloads[-1]["claims"] == []
    assert provider.requests == []


@pytest.mark.api
def test_streams_invalid_generation_as_failure_not_degraded_mode(auth_context) -> None:
    timeout = GenerationProviderError(GenerationFailure.TIMEOUT, retryable=True)
    provider = FakeStreamingProvider([timeout, timeout])
    provider.outcomes[-1] = GenerationProviderError(
        GenerationFailure.MALFORMED_OUTPUT,
        retryable=False,
    )
    service, _ = ask_service(EvidenceSet((evidence(),), "bm25", 1, 2), provider)

    response = client(service, auth_context.authenticator).post(
        "/v1/ask",
        json={"question": "Will this time out?"},
        headers=auth_context.headers(),
    )
    payloads = events(response.text)

    assert response.status_code == 200
    assert payloads[-1]["status"] == "failed"
    assert payloads[-1]["answer"] is None
    assert payloads[-1]["claims"] == []
    assert payloads[-1]["sources"] == []
    assert payloads[-1]["error"] == {
        "code": "generation_malformed_output",
        "message": "generation temporarily unavailable",
    }
    assert payloads[-1]["abstention"] is None
    assert payloads[-1]["degraded"] == {"active": False, "mode": None, "reason": None}
    assert len(provider.requests) == 2


@pytest.mark.api
def test_returns_authorized_evidence_when_generation_times_out(auth_context) -> None:
    timeout = GenerationProviderError(GenerationFailure.TIMEOUT, retryable=True)
    provider = FakeStreamingProvider([timeout, timeout])
    service, _ = ask_service(EvidenceSet((evidence(),), "bm25", 1, 2), provider)

    response = client(service, auth_context.authenticator).post(
        "/v1/ask",
        json={"question": "Will this time out?"},
        headers=auth_context.headers(),
    )
    payloads = events(response.text)

    assert response.status_code == 200
    assert payloads[-1]["status"] == "degraded"
    assert payloads[-1]["answer"] is None
    assert payloads[-1]["claims"] == []
    assert payloads[-1]["sources"] == [
        {
            "evidence_id": "chunk-1",
            "source_id": "source-1",
            "document_id": "document-1",
            "document_version_id": "version-2",
            "span": [5, 45],
        }
    ]
    assert payloads[-1]["error"] == {
        "code": "generation_timeout",
        "message": "generation temporarily unavailable",
    }
    assert payloads[-1]["abstention"] is None
    assert payloads[-1]["degraded"] == {
        "active": True,
        "mode": "evidence_only",
        "reason": "generation_timeout",
    }
    assert len(provider.requests) == 2


@pytest.mark.api
def test_marks_embedding_failure_as_explicit_bm25_degraded_mode(auth_context) -> None:
    provider = FakeStreamingProvider([(completed(),)])
    result = EvidenceSet(
        (evidence(),),
        "bm25",
        1,
        3,
        degraded=True,
        degradation_reason="embedding_provider_unavailable",
    )
    service, _ = ask_service(result, provider)

    response = client(service, auth_context.authenticator).post(
        "/v1/ask",
        json={"question": "What provides access?"},
        headers=auth_context.headers(),
    )

    assert events(response.text)[-1]["degraded"] == {
        "active": True,
        "mode": "bm25_only",
        "reason": "embedding_provider_unavailable",
    }


@pytest.mark.api
def test_rejects_missing_or_invalid_authenticated_requests(auth_context) -> None:
    service, _ = ask_service(EvidenceSet((), "bm25", 0, 1), FakeStreamingProvider([]))
    api = client(service, auth_context.authenticator)

    missing = api.post("/v1/ask", json={"question": "hello"})
    malformed = api.post(
        "/v1/ask",
        json={"question": "hello"},
        headers={"Authorization": "Bearer not-a-jwt"},
    )
    invalid = api.post("/v1/ask", json={"question": ""}, headers=auth_context.headers())
    unknown = client(service, reject_unknown_principal).post(
        "/v1/ask",
        json={"question": "hello"},
        headers=auth_context.headers(),
    )

    assert missing.status_code == 401
    assert missing.json() == {"detail": "authentication required"}
    assert malformed.status_code == 401
    assert malformed.json() == {"detail": "invalid credentials"}
    assert unknown.status_code == 403
    assert unknown.json() == {"detail": "principal unauthorized"}
    assert invalid.status_code == 422


@pytest.mark.api
def test_cancelled_session_stops_before_generation() -> None:
    provider = FakeStreamingProvider([(completed(),)])
    service, _ = ask_service(EvidenceSet((evidence(),), "bm25", 1, 2), provider)
    session = service.start(
        Question("question-1", "What provides access?"),
        Principal("alice", "alpha", (), (), True),
    )
    iterator = iter(session)

    assert next(iterator).payload == {"type": "status", "status": "retrieving"}
    session.cancel()
    assert list(iterator) == []
    assert session.cancelled is True
    assert provider.requests == []


@pytest.mark.api
def test_asgi_client_disconnect_cancels_the_request_session(auth_context) -> None:
    class TrackingExecutor:
        session: AskSession | None = None

        def start(self, question: Question, principal: Principal) -> AskSession:
            def stream() -> Generator[AskEvent]:
                yield AskEvent({"type": "status", "status": "retrieving"})
                yield AskEvent({"type": "status", "status": "generating"})

            self.session = AskSession(stream())
            return self.session

    executor = TrackingExecutor()
    app = FastAPI()
    app.include_router(create_ask_router(executor, auth_context.authenticator))
    body = json.dumps({"question": "What provides access?"}).encode()
    messages: list[dict[str, object]] = [
        {"type": "http.request", "body": body, "more_body": False},
        {"type": "http.disconnect"},
    ]
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message: dict[str, object]) -> None:
        sent.append(message)

    authorization = f"Bearer {auth_context.token()}".encode()
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/ask",
        "raw_path": b"/v1/ask",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode()),
            (b"authorization", authorization),
        ],
        "client": ("127.0.0.1", 1234),
        "server": ("testserver", 80),
    }

    async def invoke() -> None:
        await app(scope, receive, send)

    anyio.run(invoke)

    assert executor.session is not None
    assert executor.session.cancelled is True
    assert any(message["type"] == "http.response.start" for message in sent)
