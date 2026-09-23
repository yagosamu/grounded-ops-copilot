"""Production composition for the executable Search and Ask API."""

import os
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass

import boto3
from fastapi import FastAPI
from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection
from sqlalchemy import URL, Engine, create_engine, text

from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.opensearch.search import OpenSearchBM25Adapter
from adapters.postgres.audit_store import PostgresAuditStore
from adapters.postgres.ingestion_repository import IngestionRepository
from grounded_ops.app import create_app
from interfaces.http.ask import AskService
from interfaces.http.auth import JwtAuthenticator
from modules.answering.abstention import AbstentionDecider
from modules.answering.context_packer import ContextPacker
from modules.answering.generator import (
    OpenAIGenerationProvider,
    StreamingGenerationProvider,
)
from modules.answering.verifier import CitationVerifier
from modules.audit.recorder import AuditEvent, AuditRecorder, StoredAuditQuery
from modules.policy.enforcement import DocumentPolicy, DocumentRef, PolicyEnforcer
from modules.retrieval.retriever import BM25Retriever

GENERATION_MODEL = "gpt-4o-mini"
_REQUIRED = (
    "POSTGRES_PASSWORD",
    "OPENSEARCH_URL",
    "OPENAI_API_KEY",
    "JWT_VERIFICATION_KEY",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "AUDIT_REDACTION_KEY",
)


class RuntimeConfigurationError(ValueError):
    """Required configuration is missing or unsafe; do not expose API routes."""


@dataclass(frozen=True)
class RuntimeSettings:
    postgres_host: str
    postgres_port: int
    postgres_user: str
    postgres_db: str
    postgres_password: str
    opensearch_url: str
    opensearch_auth_mode: str
    aws_region: str
    openai_api_key: str
    jwt_verification_key: str
    jwt_algorithm: str
    jwt_issuer: str
    jwt_audience: str
    audit_redaction_key: bytes

    @classmethod
    def from_environment(cls, environment: Mapping[str, str]) -> "RuntimeSettings":
        if any(not environment.get(name) for name in _REQUIRED):
            raise RuntimeConfigurationError("required runtime configuration is missing")
        try:
            port = int(environment.get("POSTGRES_PORT", "5432"))
        except ValueError as error:
            raise RuntimeConfigurationError("invalid database port") from error
        if not 1 <= port <= 65535:
            raise RuntimeConfigurationError("invalid database port")
        mode = environment.get("OPENSEARCH_AUTH_MODE", "aws")
        url = environment["OPENSEARCH_URL"]
        if mode == "aws" and not url.startswith("https://"):
            raise RuntimeConfigurationError("AWS search requires HTTPS")
        if mode == "local" and not url.startswith("http://"):
            raise RuntimeConfigurationError("local search requires HTTP")
        if mode not in {"aws", "local"}:
            raise RuntimeConfigurationError("invalid search authentication mode")
        algorithm = environment.get("JWT_ALGORITHM", "RS256")
        if algorithm not in {"RS256", "ES256", "HS256"}:
            raise RuntimeConfigurationError("invalid JWT algorithm")
        return cls(
            environment.get("POSTGRES_HOST", "postgres"),
            port,
            environment.get("POSTGRES_USER", "grounded_ops"),
            environment.get("POSTGRES_DB", "grounded_ops"),
            environment["POSTGRES_PASSWORD"],
            url,
            mode,
            environment.get("AWS_REGION", "us-east-1"),
            environment["OPENAI_API_KEY"],
            environment["JWT_VERIFICATION_KEY"],
            algorithm,
            environment["JWT_ISSUER"],
            environment["JWT_AUDIENCE"],
            environment["AUDIT_REDACTION_KEY"].encode(),
        )


class _PolicyStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def get_document_policies(
        self, tenant_id: str, references: tuple[DocumentRef, ...]
    ) -> Mapping[DocumentRef, DocumentPolicy]:
        with self._engine.connect() as connection:
            return IngestionRepository(connection).get_document_policies(
                tenant_id, references
            )


class _AuditStore:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    def append(self, event: AuditEvent) -> None:
        with self._engine.begin() as connection:
            PostgresAuditStore(connection).append(event)

    def query(self, query: StoredAuditQuery) -> tuple[AuditEvent, ...]:
        with self._engine.connect() as connection:
            return PostgresAuditStore(connection).query(query)


def _search_client(settings: RuntimeSettings) -> OpenSearch:
    if settings.opensearch_auth_mode == "local":
        return OpenSearch(hosts=[settings.opensearch_url], timeout=5, max_retries=1)
    credentials = boto3.Session().get_credentials()
    if credentials is None:
        raise RuntimeConfigurationError("AWS task credentials are unavailable")
    return OpenSearch(
        hosts=[settings.opensearch_url],
        http_auth=AWSV4SignerAuth(credentials, settings.aws_region, "es"),
        use_ssl=True,
        verify_certs=True,
        connection_class=RequestsHttpConnection,
        timeout=5,
        max_retries=1,
    )


def _database_ready(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            return bool(connection.execute(text("SELECT 1")).scalar_one() == 1)
    except Exception:
        return False


def _search_ready(client: OpenSearch) -> bool:
    try:
        return bool(client.ping())
    except Exception:
        return False


def create_runtime_app(
    environment: Mapping[str, str] | None = None,
    *,
    generation_provider: StreamingGenerationProvider | None = None,
) -> FastAPI:
    """Build the production API, or expose only health routes when unsafe."""
    try:
        settings = RuntimeSettings.from_environment(
            os.environ if environment is None else environment
        )
    except RuntimeConfigurationError:
        return create_app(health_dependencies={"configuration": lambda: False})

    url = URL.create(
        "postgresql+psycopg",
        username=settings.postgres_user,
        password=settings.postgres_password,
        host=settings.postgres_host,
        port=settings.postgres_port,
        database=settings.postgres_db,
    )
    engine = create_engine(url, pool_pre_ping=True)
    try:
        client = _search_client(settings)
    except RuntimeConfigurationError:
        engine.dispose()
        return create_app(health_dependencies={"configuration": lambda: False})
    try:
        enforcer = PolicyEnforcer(
            _PolicyStore(engine),
            AuditRecorder(_AuditStore(engine), settings.audit_redaction_key),
        )
        retriever = BM25Retriever(
            OpenSearchBM25Adapter(client, LexicalIndexSchema(client).read_alias),
            enforcer,
        )
        provider = generation_provider or OpenAIGenerationProvider(
            api_key=settings.openai_api_key,
            model=GENERATION_MODEL,
        )
        ask = AskService(
            retriever,
            ContextPacker(max_tokens=1200),
            provider,
            CitationVerifier(enforcer),
            AbstentionDecider(),
        )
        authenticator = JwtAuthenticator(
            verification_key=settings.jwt_verification_key,
            issuer=settings.jwt_issuer,
            audience=settings.jwt_audience,
            algorithms=(settings.jwt_algorithm,),
        )

        @asynccontextmanager
        async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
            try:
                yield
            finally:
                client.close()
                engine.dispose()

        return create_app(
            retriever=retriever,
            ask_executor=ask,
            authenticator=authenticator,
            health_dependencies={
                "postgres": lambda: _database_ready(engine),
                "opensearch": lambda: _search_ready(client),
            },
            lifespan=lifespan,
        )
    except Exception:
        client.close()
        engine.dispose()
        raise
