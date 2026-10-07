"""Authenticated session validation and durable answer feedback for the UI."""

import pytest
from conftest import AuthContext
from fastapi.testclient import TestClient

from grounded_ops.app import create_app
from modules.policy.authorizer import Principal


class MemoryFeedbackStore:
    def __init__(self) -> None:
        self.rows: list[tuple[Principal, str, str]] = []

    def record(self, principal: Principal, answer_id: str, rating: str) -> None:
        self.rows.append((principal, answer_id, rating))


@pytest.mark.api
def test_session_requires_valid_jwt_and_returns_only_safe_identity(
    auth_context: AuthContext,
) -> None:
    client = TestClient(create_app(authenticator=auth_context.authenticator))

    assert client.get("/v1/session").status_code == 401
    response = client.get("/v1/session", headers=auth_context.headers())

    assert response.status_code == 200
    assert response.json() == {"principal_id": "alice", "tenant_id": "alpha"}


@pytest.mark.api
def test_feedback_is_authenticated_validated_and_tenant_bound(
    auth_context: AuthContext,
) -> None:
    store = MemoryFeedbackStore()
    client = TestClient(
        create_app(authenticator=auth_context.authenticator, feedback_store=store)
    )
    body = {"answer_id": "ans-123", "rating": "helpful"}

    assert client.post("/v1/feedback", json=body).status_code == 401
    assert (
        client.post(
            "/v1/feedback",
            headers=auth_context.headers(),
            json={**body, "rating": "bad"},
        ).status_code
        == 422
    )
    response = client.post("/v1/feedback", headers=auth_context.headers(), json=body)

    assert response.status_code == 201
    assert response.json() == {"recorded": True}
    assert store.rows == [
        (
            Principal("alice", "alpha", ("engineer", "reader"), ("oncall",)),
            "ans-123",
            "helpful",
        )
    ]


@pytest.mark.api
def test_feedback_storage_failure_is_not_reported_as_success(
    auth_context: AuthContext,
) -> None:
    class FailingStore:
        def record(self, principal: Principal, answer_id: str, rating: str) -> None:
            raise RuntimeError("database unavailable")

    client = TestClient(
        create_app(
            authenticator=auth_context.authenticator, feedback_store=FailingStore()
        )
    )
    response = client.post(
        "/v1/feedback",
        headers=auth_context.headers(),
        json={"answer_id": "ans-123", "rating": "not_helpful"},
    )
    assert response.status_code == 503
    assert response.json() == {"detail": "feedback temporarily unavailable"}
