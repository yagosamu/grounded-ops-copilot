"""Liveness and dependency-aware readiness HTTP routes."""

from __future__ import annotations

from collections.abc import Callable, Mapping

from fastapi import APIRouter
from fastapi.responses import JSONResponse

DependencyProbe = Callable[[], bool]


def create_health_router(dependencies: Mapping[str, DependencyProbe]) -> APIRouter:
    """Create health routes that expose dependency names but not configuration."""
    router = APIRouter(tags=["health"])

    @router.get("/health/live")
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/health/ready", response_model=None)
    def readiness() -> dict[str, object] | JSONResponse:
        unavailable = [name for name, probe in dependencies.items() if not probe()]
        if unavailable:
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "dependencies": unavailable},
            )
        return {"status": "ok", "dependencies": []}

    return router
