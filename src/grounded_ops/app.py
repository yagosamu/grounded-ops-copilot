"""Application factory for the GroundedOps HTTP API."""

from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager

from fastapi import FastAPI

from interfaces.http.ask import AskExecutor, create_ask_router
from interfaces.http.auth import PrincipalDependency
from interfaces.http.health import DependencyProbe, create_health_router
from interfaces.http.investigations import InvestigationAPI, create_investigation_router
from interfaces.http.search import Retriever, create_search_router
from observability.telemetry import CorrelationMiddleware, Telemetry


def create_app(
    retriever: Retriever | None = None,
    ask_executor: AskExecutor | None = None,
    investigation_lifecycle: InvestigationAPI | None = None,
    authenticator: PrincipalDependency | None = None,
    telemetry: Telemetry | None = None,
    health_dependencies: Mapping[str, DependencyProbe] | None = None,
    lifespan: Callable[[FastAPI], AbstractAsyncContextManager[None]] | None = None,
) -> FastAPI:
    """Create the HTTP application without connecting to infrastructure."""
    app = FastAPI(title="GroundedOps", lifespan=lifespan)
    app.add_middleware(CorrelationMiddleware, telemetry=telemetry or Telemetry())
    app.include_router(create_health_router(health_dependencies or {}))
    protected = (retriever, ask_executor, investigation_lifecycle)
    if any(item is not None for item in protected) and authenticator is None:
        raise ValueError("protected routes require an authenticator")
    if retriever is not None:
        if authenticator is None:
            raise ValueError("protected routes require an authenticator")
        app.include_router(create_search_router(retriever, authenticator))
    if ask_executor is not None:
        if authenticator is None:
            raise ValueError("protected routes require an authenticator")
        app.include_router(create_ask_router(ask_executor, authenticator))
    if investigation_lifecycle is not None:
        if authenticator is None:
            raise ValueError("protected routes require an authenticator")
        app.include_router(
            create_investigation_router(investigation_lifecycle, authenticator)
        )
    return app
