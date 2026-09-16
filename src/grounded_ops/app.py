"""Application factory for the GroundedOps HTTP API."""

from fastapi import FastAPI

from interfaces.http.health import create_health_router


def create_app() -> FastAPI:
    """Create the HTTP application without connecting to infrastructure."""
    app = FastAPI(title="GroundedOps")
    app.include_router(create_health_router({}))
    return app
