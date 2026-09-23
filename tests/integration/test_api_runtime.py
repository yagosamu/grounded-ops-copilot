"""The executable API composes real PostgreSQL and OpenSearch dependencies."""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from uuid import uuid4

import jwt
import pytest
from fastapi.testclient import TestClient
from opensearchpy import OpenSearch
from sqlalchemy import URL, create_engine, text

import grounded_ops.runtime as runtime
from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.opensearch.index_writer import (
    IndexChunk,
    OpenSearchIndexWriter,
    VersionProjection,
)
from adapters.policy.snapshot import SnapshotPolicyStore
from adapters.postgres.migrations import migrate
from grounded_ops.runtime import create_runtime_app
from modules.answering.generator import (
    GenerationCompleted,
    GenerationRequest,
    GenerationResponse,
    ProposedCitation,
    ProposedClaim,
)
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer

pytestmark = pytest.mark.integration


def test_configured_api_serves_authorized_search_and_verified_ask(
    postgres_url: URL,
    opensearch_client: OpenSearch,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_id = f"source-{uuid4().hex}"
    document_id = f"document-{uuid4().hex}"
    version_id = f"version-{uuid4().hex}"
    chunk_id = f"chunk-{uuid4().hex}"
    content = "TracerProvider provides access to tracers."
    timestamp = datetime(2026, 9, 17, tzinfo=UTC)
    schema = LexicalIndexSchema(opensearch_client)
    signing_key = uuid4().hex + uuid4().hex
    database = create_engine(postgres_url)
    try:
        with database.begin() as connection:
            migrate(connection, "head")
            connection.execute(
                text("""
                    INSERT INTO sources (tenant_id,id,type,external_ref,policy)
                    VALUES (
                        'alpha',:source,'markdown',:external_ref,'["public"]'::jsonb
                    )
                """),
                {"source": source_id, "external_ref": source_id},
            )
            connection.execute(
                text("""
                    INSERT INTO documents (tenant_id,id,source_id,canonical_key)
                    VALUES ('alpha',:document,:source,:canonical_key)
                """),
                {
                    "document": document_id,
                    "source": source_id,
                    "canonical_key": document_id,
                },
            )
            connection.execute(
                text("""
                    INSERT INTO document_versions
                    (tenant_id,id,document_id,source_version,content_hash,
                     source_timestamp,parser_version)
                    VALUES ('alpha',:version,:document,'v1',:hash,:stamp,'markdown-v1')
                """),
                {
                    "version": version_id,
                    "document": document_id,
                    "hash": sha256(content.encode()).hexdigest(),
                    "stamp": timestamp,
                },
            )
            connection.execute(
                text("""
                    UPDATE documents SET current_version_id=:version
                    WHERE tenant_id='alpha' AND id=:document
                """),
                {"version": version_id, "document": document_id},
            )
        schema.ensure("1")
        projection = VersionProjection(
            "alpha",
            source_id,
            document_id,
            version_id,
            ("public",),
            timestamp,
            (
                IndexChunk(
                    chunk_id,
                    0,
                    content,
                    0,
                    len(content),
                    sha256(content.encode()).hexdigest(),
                    "markdown-v1",
                    "structural-v1",
                ),
            ),
        )
        policy = DocumentPolicy(
            "alpha", source_id, document_id, version_id, ("public",), False
        )
        OpenSearchIndexWriter(
            opensearch_client,
            schema.write_alias,
            PolicyEnforcer(SnapshotPolicyStore((policy,))),
        ).upsert(projection)

        class Provider:
            def stream(self, request: GenerationRequest):
                assert request.context.evidence[0].chunk_id == chunk_id
                yield GenerationCompleted(
                    GenerationResponse(
                        (
                            ProposedClaim(
                                content,
                                (
                                    ProposedCitation(
                                        chunk_id, version_id, (0, len(content))
                                    ),
                                ),
                            ),
                        ),
                        "test-model",
                        12,
                        8,
                    )
                )

        environment = {
            "POSTGRES_HOST": str(postgres_url.host),
            "POSTGRES_PORT": str(postgres_url.port),
            "POSTGRES_USER": str(postgres_url.username),
            "POSTGRES_DB": str(postgres_url.database),
            "POSTGRES_PASSWORD": str(postgres_url.password),
            "OPENSEARCH_URL": f"http://127.0.0.1:{opensearch_client.transport.hosts[0]['port']}",
            "OPENSEARCH_AUTH_MODE": "local",
            "OPENAI_API_KEY": "test-only-key",
            "JWT_VERIFICATION_KEY": signing_key,
            "JWT_ALGORITHM": "HS256",
            "JWT_ISSUER": "https://identity.groundedops.test",
            "JWT_AUDIENCE": "grounded-ops-api",
            "AUDIT_REDACTION_KEY": uuid4().hex,
        }
        with TestClient(
            create_runtime_app(environment, generation_provider=Provider())
        ) as client:
            assert client.get("/health/ready").json() == {
                "status": "ok",
                "dependencies": [],
            }
            assert (
                client.get("/v1/evidence/search", params={"q": "tracer"}).status_code
                == 401
            )
            alpha = _token(signing_key, "alpha")
            beta = _token(signing_key, "beta")
            search = client.get(
                "/v1/evidence/search",
                params={"q": "TracerProvider"},
                headers={"Authorization": f"Bearer {alpha}"},
            )
            assert search.status_code == 200
            assert [item["chunk_id"] for item in search.json()["evidence"]] == [
                chunk_id
            ]
            other = client.get(
                "/v1/evidence/search",
                params={"q": "TracerProvider"},
                headers={"Authorization": f"Bearer {beta}"},
            )
            assert other.status_code == 200
            assert other.json()["evidence"] == []
            answer = client.post(
                "/v1/ask",
                json={"question": "What does TracerProvider do?"},
                headers={"Authorization": f"Bearer {alpha}"},
            )
            assert answer.status_code == 200
            events = [json.loads(line) for line in answer.text.splitlines()]
            assert events[-1]["status"] == "verified"
            assert events[-1]["claims"][0]["citations"][0]["evidence_id"] == chunk_id
            monkeypatch.setattr(runtime, "_search_ready", lambda _client: False)
            readiness = client.get("/health/ready")
            assert readiness.status_code == 503
            assert readiness.json()["dependencies"] == ["opensearch"]
            monkeypatch.setattr(runtime, "_database_ready", lambda _engine: False)
            readiness = client.get("/health/ready")
            assert readiness.status_code == 503
            assert readiness.json()["dependencies"] == ["postgres", "opensearch"]
        with database.connect() as connection:
            audit_count = connection.execute(
                text("SELECT count(*) FROM audit_events WHERE category='authorization'")
            ).scalar_one()
        assert audit_count >= 2
    finally:
        database.dispose()
        opensearch_client.indices.delete(
            index=schema.index_name("1"), ignore_unavailable=True
        )


def _token(key: str, tenant_id: str) -> str:
    now = datetime.now(UTC)
    return jwt.encode(
        {
            "sub": "alice",
            "tenant_id": tenant_id,
            "iss": "https://identity.groundedops.test",
            "aud": "grounded-ops-api",
            "iat": now,
            "exp": now + timedelta(minutes=5),
        },
        key,
        algorithm="HS256",
    )
