"""Rebuild a disposable search projection from durable document evidence."""

from dataclasses import dataclass
from hashlib import sha256

from opensearchpy import OpenSearch
from sqlalchemy import Engine, text

from adapters.object_store.document_store import ArtifactRef, DocumentStore
from adapters.opensearch.index_writer import (
    IndexChunk,
    OpenSearchIndexWriter,
    VersionProjection,
)
from adapters.postgres.ingestion_repository import IngestionRepository
from modules.chunking.structural import StructuralChunker
from modules.parsing.parser import MarkdownParser
from modules.policy.enforcement import PolicyEnforcer


class ReplayIntegrityError(RuntimeError):
    """Authoritative metadata and artifacts cannot produce a complete index."""


@dataclass(frozen=True)
class ReplaySummary:
    documents: int
    versions: int
    chunks: int
    chunk_ids: tuple[str, ...]


def replay_authoritative(
    engine: Engine,
    store: DocumentStore,
    client: OpenSearch,
    candidate_index: str,
    *,
    chunk_max_tokens: int,
) -> ReplaySummary:
    """Write only to an unaliased candidate while ingestion writes are paused."""
    parser = MarkdownParser()
    chunker = StructuralChunker(chunk_max_tokens)
    with engine.connect() as connection:
        repository = IngestionRepository(connection)
        writer = OpenSearchIndexWriter(
            client, candidate_index, PolicyEnforcer(repository)
        )
        active_documents = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM documents "
                    "WHERE NOT deleted AND current_version_id IS NOT NULL"
                )
            ).scalar_one()
        )
        rows = (
            connection.execute(
                text("""
                SELECT v.tenant_id,v.id AS version_id,v.document_id,
                    v.content_hash,v.source_timestamp,v.raw_ref,v.normalized_ref,
                    d.source_id,d.current_version_id,s.policy
                FROM document_versions v
                JOIN documents d ON d.tenant_id=v.tenant_id AND d.id=v.document_id
                JOIN sources s ON s.tenant_id=d.tenant_id AND s.id=d.source_id
                JOIN ingestion_jobs j ON j.tenant_id=v.tenant_id
                    AND j.version_id=v.id AND j.state='completed'
                WHERE NOT d.deleted AND d.current_version_id IS NOT NULL
                ORDER BY v.tenant_id,v.document_id,
                    (v.id=d.current_version_id),v.source_timestamp,v.source_version
            """)
            )
            .mappings()
            .all()
        )
        current_count = sum(
            row["version_id"] == row["current_version_id"] for row in rows
        )
        if current_count != active_documents:
            raise ReplayIntegrityError("current version is not restorable")
        chunk_count = 0
        chunk_ids: list[str] = []
        for row in rows:
            tenant = str(row["tenant_id"])
            raw = store.get(tenant, _reference(tenant, "raw", row["raw_ref"]))
            normalized = store.get(
                tenant, _reference(tenant, "normalized", row["normalized_ref"])
            )
            if sha256(raw).hexdigest() != row["content_hash"]:
                raise ReplayIntegrityError("raw content does not match metadata")
            parsed = parser.parse(raw)
            if parsed.normalized_text.encode("utf-8") != normalized:
                raise ReplayIntegrityError(
                    "normalized content does not match raw artifact"
                )
            chunks = chunker.chunk(parsed)
            if not chunks:
                continue
            version_chunk_ids = tuple(
                sha256(f"{row['version_id']}:{chunk.ordinal}".encode()).hexdigest()
                for chunk in chunks
            )
            writer.upsert(
                VersionProjection(
                    tenant,
                    str(row["source_id"]),
                    str(row["document_id"]),
                    str(row["version_id"]),
                    tuple(str(value) for value in row["policy"]),
                    row["source_timestamp"],
                    tuple(
                        IndexChunk(
                            version_chunk_ids[chunk.ordinal],
                            chunk.ordinal,
                            chunk.text,
                            chunk.start,
                            chunk.end,
                            sha256(chunk.text.encode()).hexdigest(),
                            chunk.parser_version,
                            chunk.chunker_version,
                        )
                        for chunk in chunks
                    ),
                )
            )
            chunk_count += len(chunks)
            chunk_ids.extend(version_chunk_ids)
        return ReplaySummary(active_documents, len(rows), chunk_count, tuple(chunk_ids))


def _reference(tenant: str, kind: str, key: str | None) -> ArtifactRef:
    if key is None:
        raise ReplayIntegrityError("artifact reference is missing")
    parts = key.split("/")
    if len(parts) != 3 or parts[0] != tenant or parts[1] != kind:
        raise ReplayIntegrityError("artifact reference is invalid")
    try:
        return ArtifactRef(tenant, kind, parts[2])
    except ValueError:
        raise ReplayIntegrityError("artifact reference is invalid") from None
