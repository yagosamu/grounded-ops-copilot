"""API tests for liveness and dependency-aware readiness."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interfaces.http.health import create_health_router

pytestmark = pytest.mark.api


def application_with(*, database_ready: bool) -> FastAPI:
    """Create a route seam with a deterministic database probe."""
    app = FastAPI()
    app.include_router(create_health_router({"postgres": lambda: database_ready}))
    return app


def test_liveness_remains_healthy_when_dependency_is_unavailable() -> None:
    response = TestClient(application_with(database_ready=False)).get("/health/live")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_reports_healthy_dependencies() -> None:
    response = TestClient(application_with(database_ready=True)).get("/health/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "dependencies": []}


def test_readiness_identifies_unavailable_dependency_without_configuration() -> None:
    response = TestClient(application_with(database_ready=False)).get("/health/ready")

    assert response.status_code == 503
    assert response.json() == {"status": "unavailable", "dependencies": ["postgres"]}
    assert "local-validation-password" not in response.text
