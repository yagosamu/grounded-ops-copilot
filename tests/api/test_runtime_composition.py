"""Executable API must fail closed until every production dependency is configured."""

import pytest
from fastapi.testclient import TestClient

from grounded_ops.runtime import GENERATION_MODEL, RuntimeSettings, create_runtime_app

pytestmark = pytest.mark.api

REQUIRED = (
    "POSTGRES_PASSWORD",
    "OPENSEARCH_URL",
    "OPENAI_API_KEY",
    "JWT_VERIFICATION_KEY",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "AUDIT_REDACTION_KEY",
)


def test_unconfigured_container_keeps_liveness_but_exposes_no_protected_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)

    with TestClient(create_runtime_app()) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        readiness = client.get("/health/ready")
        assert readiness.status_code == 503
        assert readiness.json() == {
            "status": "unavailable",
            "dependencies": ["configuration"],
        }
        assert (
            client.get("/v1/evidence/search", params={"q": "tracing"}).status_code
            == 404
        )
        assert client.post("/v1/ask", json={"question": "tracing"}).status_code == 404


def test_partial_configuration_does_not_expose_secret_or_routes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in REQUIRED:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-private-key")

    with TestClient(create_runtime_app()) as client:
        readiness = client.get("/health/ready")
        assert readiness.status_code == 503
        assert "test-only-private-key" not in readiness.text
        assert (
            client.get("/v1/evidence/search", params={"q": "tracing"}).status_code
            == 404
        )


def test_explicit_empty_environment_never_inherits_host_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for key in REQUIRED:
        monkeypatch.setenv(key, "host-secret")
    with TestClient(create_runtime_app({})) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.post("/v1/ask", json={"question": "tracing"}).status_code == 404


def test_unsafe_search_or_jwt_configuration_fails_closed() -> None:
    environment = dict.fromkeys(REQUIRED, "test-only-value")
    environment["OPENSEARCH_URL"] = "http://search.example.test:9200"
    with TestClient(create_runtime_app(environment)) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.get("/v1/evidence/search", params={"q": "x"}).status_code == 404

    environment["OPENSEARCH_AUTH_MODE"] = "local"
    environment["JWT_ALGORITHM"] = "none"
    with TestClient(create_runtime_app(environment)) as client:
        assert client.get("/health/ready").status_code == 503
        assert client.post("/v1/ask", json={"question": "x"}).status_code == 404


def test_runtime_defaults_to_aws_identity_and_selected_model() -> None:
    environment = dict.fromkeys(REQUIRED, "test-only-value")
    environment["OPENSEARCH_URL"] = "https://search.example.test"
    settings = RuntimeSettings.from_environment(environment)
    assert settings.opensearch_auth_mode == "aws"
    assert settings.jwt_algorithm == "RS256"
    assert GENERATION_MODEL == "gpt-4o-mini"
