"""Project authoritative document versions into a lexical index."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from opensearchpy import OpenSearch

from modules.policy.enforcement import DocumentRef, PolicyEnforcer


@dataclass(frozen=True)
class IndexChunk:
    chunk_id: str
    ordinal: int
    text: str
    start: int
    end: int
    content_hash: str
    parser_version: str
    chunker_version: str


@dataclass(frozen=True)
class VersionProjection:
    tenant_id: str
    source_id: str
    document_id: str
    document_version_id: str
    policy: tuple[str, ...]
    source_timestamp: datetime
    chunks: tuple[IndexChunk, ...]


class BulkProjectionError(RuntimeError):
    def __init__(self, failed_chunk_ids: tuple[str, ...]) -> None:
        super().__init__("document version projection failed")
        self.failed_chunk_ids = failed_chunk_ids


class OpenSearchIndexWriter:
    def __init__(
        self, client: OpenSearch, write_alias: str, enforcer: PolicyEnforcer
    ) -> None:
        self.client = client
        self.write_alias = write_alias
        self._enforcer = enforcer

    def upsert(self, projection: VersionProjection) -> None:
        self._enforcer.validate_projections((projection,))
        body: list[dict[str, Any]] = []
        for chunk in projection.chunks:
            body.extend(
                (
                    {"index": {"_index": self.write_alias, "_id": chunk.chunk_id}},
                    {
                        "chunk_id": chunk.chunk_id,
                        "tenant_id": projection.tenant_id,
                        "source_id": projection.source_id,
                        "document_id": projection.document_id,
                        "document_version_id": projection.document_version_id,
                        "policy": list(projection.policy),
                        "ordinal": chunk.ordinal,
                        "text": chunk.text,
                        "start": chunk.start,
                        "end": chunk.end,
                        "content_hash": chunk.content_hash,
                        "parser_version": chunk.parser_version,
                        "chunker_version": chunk.chunker_version,
                        "source_timestamp": projection.source_timestamp.isoformat(),
                        "is_current": False,
                    },
                )
            )
        response = cast(dict[str, Any], self.client.bulk(body=body, refresh="wait_for"))
        failed: list[str] = []
        successful: list[str] = []
        for item in response.get("items", []):
            result = item["index"]
            (failed if result.get("error") else successful).append(result["_id"])
        if failed:
            if successful:
                self.client.delete_by_query(
                    index=self.write_alias,
                    body={"query": {"ids": {"values": successful}}},
                    refresh=True,
                )
            raise BulkProjectionError(tuple(failed))

        common_filters = [
            {"term": {"tenant_id": projection.tenant_id}},
            {"term": {"document_id": projection.document_id}},
        ]
        self.client.update_by_query(
            index=self.write_alias,
            body={
                "query": {
                    "bool": {
                        "filter": common_filters,
                        "must_not": {
                            "term": {
                                "document_version_id": projection.document_version_id
                            }
                        },
                    }
                },
                "script": {"source": "ctx._source.is_current = false"},
            },
            refresh=True,
            conflicts="proceed",
        )
        self.client.update_by_query(
            index=self.write_alias,
            body={
                "query": {
                    "bool": {
                        "filter": common_filters
                        + [
                            {
                                "term": {
                                    "document_version_id": (
                                        projection.document_version_id
                                    )
                                }
                            }
                        ]
                    }
                },
                "script": {"source": "ctx._source.is_current = true"},
            },
            refresh=True,
            conflicts="proceed",
        )

    def remove_document(self, tenant_id: str, document_id: str) -> None:
        self._enforcer.validate_removal(DocumentRef(tenant_id, document_id))
        self.client.delete_by_query(
            index=self.write_alias,
            body={
                "query": {
                    "bool": {
                        "filter": [
                            {"term": {"tenant_id": tenant_id}},
                            {"term": {"document_id": document_id}},
                        ]
                    }
                }
            },
            refresh=True,
            conflicts="proceed",
        )
