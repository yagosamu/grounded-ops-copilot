"""Application factory for the GroundedOps HTTP API."""

from fastapi import FastAPI


def create_app() -> FastAPI:
    """Create the HTTP application without connecting to infrastructure."""
    return FastAPI(title="GroundedOps")
