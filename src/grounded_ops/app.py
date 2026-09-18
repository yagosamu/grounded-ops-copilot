"""Application factory for the GroundedOps HTTP API."""

from fastapi import FastAPI

from interfaces.http.ask import AskExecutor, create_ask_router
from interfaces.http.auth import PrincipalDependency
from interfaces.http.health import create_health_router
from interfaces.http.search import Retriever, create_search_router


def create_app(
    retriever: Retriever | None = None,
    ask_executor: AskExecutor | None = None,
    authenticator: PrincipalDependency | None = None,
) -> FastAPI:
    """Create the HTTP application without connecting to infrastructure."""
    app = FastAPI(title="GroundedOps")
    app.include_router(create_health_router({}))
    if (retriever is not None or ask_executor is not None) and authenticator is None:
        raise ValueError("protected routes require an authenticator")
    if retriever is not None:
        if authenticator is None:
            raise ValueError("protected routes require an authenticator")
        app.include_router(create_search_router(retriever, authenticator))
    if ask_executor is not None:
        if authenticator is None:
            raise ValueError("protected routes require an authenticator")
        app.include_router(create_ask_router(ask_executor, authenticator))
    return app
