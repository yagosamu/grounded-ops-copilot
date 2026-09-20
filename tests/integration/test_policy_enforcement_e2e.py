"""Adversarial policy changes cannot bypass PostgreSQL-backed authorization."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from opensearchpy import OpenSearch
from sqlalchemy import Engine

from adapters.opensearch.index_schema import LexicalIndexSchema
from adapters.opensearch.index_writer import (
    IndexChunk,
    OpenSearchIndexWriter,
    VersionProjection,
)
from adapters.opensearch.search import OpenSearchBM25Adapter
from adapters.postgres.ingestion_repository import IngestionRepository
from domain.answering import (
    AnswerUsage,
    Citation,
    Claim,
    GroundedAnswer,
    Question,
    VerificationStatus,
)
from domain.ingestion import Source
from modules.answering.verifier import CitationVerifier, VerificationFailure
from modules.policy.authorizer import Principal
from modules.policy.enforcement import DocumentRef, PolicyEnforcer, PolicyViolation
from modules.retrieval.retriever import BM25Retriever, QueryContext


@pytest.mark.integration
@pytest.mark.security
def test_revoked_policy_blocks_search_metadata_and_citation_reuse(
    database: Engine, opensearch_client: OpenSearch
) -> None:
    prefix = f"test-policy-{uuid4().hex}"
    schema = LexicalIndexSchema(opensearch_client, prefix)
    schema.ensure("1")
    stamp = datetime(2026, 9, 20, tzinfo=UTC)
    try:
        with database.begin() as connection:
            repository = IngestionRepository(connection)
            enforcer = PolicyEnforcer(repository)
            source = Source(
                "runbooks",
                "alpha",
                "markdown",
                "runbooks/recovery.md",
                ("role:engineer",),
            )
            submitted = repository.submit(
                source, "recovery.md", "rev1", "a" * 64, stamp, "markdown-v1"
            )
            projection = VersionProjection(
                "alpha",
                source.id,
                submitted.document.id,
                submitted.version.id,
                source.policy,
                stamp,
                (
                    IndexChunk(
                        "recovery-chunk",
                        0,
                        "Restart the collector after draining traffic.",
                        0,
                        45,
                        "chunk-hash",
                        "markdown-v1",
                        "structural-v1",
                    ),
                ),
            )
            writer = OpenSearchIndexWriter(
                opensearch_client, schema.write_alias, enforcer
            )
            writer.upsert(projection)
            foreign_source = Source(
                "incidents",
                "beta",
                "markdown",
                "incidents/private.md",
                ("public",),
            )
            foreign = repository.submit(
                foreign_source,
                "private.md",
                "rev1",
                "b" * 64,
                stamp,
                "markdown-v1",
            )
            writer.upsert(
                VersionProjection(
                    "beta",
                    foreign_source.id,
                    foreign.document.id,
                    foreign.version.id,
                    foreign_source.policy,
                    stamp,
                    (
                        IndexChunk(
                            "private-chunk",
                            0,
                            "Private collector incident.",
                            0,
                            27,
                            "private-hash",
                            "markdown-v1",
                            "structural-v1",
                        ),
                    ),
                )
            )
            retriever = BM25Retriever(
                OpenSearchBM25Adapter(opensearch_client, schema.read_alias), enforcer
            )
            engineer = Principal("alice", "alpha", ("engineer",), ())
            before = retriever.retrieve(QueryContext("collector traffic", engineer))
            draft = GroundedAnswer(
                Question("question-1", "How should the collector restart?"),
                (
                    Claim(
                        "Restart the collector after draining traffic.",
                        (
                            Citation(
                                "recovery-chunk",
                                submitted.version.id,
                                (0, 45),
                            ),
                        ),
                    ),
                ),
                VerificationStatus.UNVERIFIED,
                AnswerUsage("gpt-test", 10, 5),
            )

            verified = CitationVerifier(enforcer).verify(draft, before, engineer)
            repository.submit(
                replace(source, policy=("role:finance",)),
                "recovery.md",
                "rev1",
                "a" * 64,
                stamp,
                "markdown-v1",
            )
            after = retriever.retrieve(QueryContext("collector traffic", engineer))
            stale_citation = CitationVerifier(enforcer).verify(draft, before, engineer)
            metadata = enforcer.authorize_reads(
                engineer,
                (DocumentRef("alpha", submitted.document.id, submitted.version.id),),
            )

            assert [item.chunk_id for item in before.evidence] == ["recovery-chunk"]
            assert verified.answer.status is VerificationStatus.VERIFIED
            assert after.evidence == ()
            assert metadata == {}
            assert stale_citation.answer.status is VerificationStatus.UNVERIFIED
            assert stale_citation.failures == (
                VerificationFailure.EVIDENCE_UNAUTHORIZED,
            )

            with pytest.raises(PolicyViolation, match="projection unauthorized"):
                writer.upsert(replace(projection, policy=("public",)))

            forged = retriever.retrieve(
                QueryContext(
                    "collector traffic",
                    engineer,
                    document_ids=(foreign.document.id,),
                )
            )
            assert forged.evidence == ()
            assert (
                enforcer.authorize_reads(
                    engineer,
                    (DocumentRef("beta", foreign.document.id, foreign.version.id),),
                )
                == {}
            )
    finally:
        opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)
