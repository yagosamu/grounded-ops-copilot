"""Application factory for the GroundedOps HTTP API."""

from fastapi import FastAPI

from interfaces.http.health import create_health_router
from interfaces.http.search import Retriever, create_search_router


def create_app(retriever: Retriever | None = None) -> FastAPI:
    """Create the HTTP application without connecting to infrastructure."""
    app = FastAPI(title="GroundedOps")
    app.include_router(create_health_router({}))
    if retriever is not None:
        app.include_router(create_search_router(retriever))
    return app
