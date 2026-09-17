"""Execute policy-filtered BM25 queries against OpenSearch."""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from opensearchpy import OpenSearch


@dataclass(frozen=True)
class SearchRequest:
    query: str
    tenant_id: str
    access_policy: tuple[str, ...]
    limit: int
    offset: int
    source_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    include_historical: bool = False


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    tenant_id: str
    source_id: str
    document_id: str
    document_version_id: str
    policy: tuple[str, ...]
    ordinal: int
    text: str
    start: int
    end: int
    content_hash: str
    parser_version: str
    chunker_version: str
    source_timestamp: datetime
    is_current: bool
    score: float


@dataclass(frozen=True)
class SearchResult:
    hits: tuple[SearchHit, ...]
    total: int
    took_ms: int


class OpenSearchBM25Adapter:
    def __init__(self, client: OpenSearch, read_alias: str) -> None:
        self.client = client
        self.read_alias = read_alias

    def search(self, request: SearchRequest) -> SearchResult:
        filters: list[dict[str, Any]] = [
            {"term": {"tenant_id": request.tenant_id}},
            {"terms": {"policy": list(request.access_policy)}},
        ]
        if not request.include_historical:
            filters.append({"term": {"is_current": True}})
        if request.source_ids:
            filters.append({"terms": {"source_id": list(request.source_ids)}})
        if request.document_ids:
            filters.append({"terms": {"document_id": list(request.document_ids)}})
        response = cast(
            dict[str, Any],
            self.client.search(
                index=self.read_alias,
                body={
                    "from": request.offset,
                    "size": request.limit,
                    "track_total_hits": True,
                    "query": {
                        "bool": {
                            "must": [{"match": {"text": request.query}}],
                            "filter": filters,
                        }
                    },
                    "sort": [{"_score": "desc"}, {"chunk_id": "asc"}],
                },
            ),
        )
        raw_hits = response["hits"]["hits"]
        hits = tuple(self._hit(hit) for hit in raw_hits)
        raw_total = response["hits"]["total"]
        total = raw_total["value"] if isinstance(raw_total, dict) else raw_total
        return SearchResult(hits, int(total), int(response["took"]))

    @staticmethod
    def _hit(hit: dict[str, Any]) -> SearchHit:
        source = hit["_source"]
        return SearchHit(
            chunk_id=str(source["chunk_id"]),
            tenant_id=str(source["tenant_id"]),
            source_id=str(source["source_id"]),
            document_id=str(source["document_id"]),
            document_version_id=str(source["document_version_id"]),
            policy=tuple(str(value) for value in source["policy"]),
            ordinal=int(source["ordinal"]),
            text=str(source["text"]),
            start=int(source["start"]),
            end=int(source["end"]),
            content_hash=str(source["content_hash"]),
            parser_version=str(source["parser_version"]),
            chunker_version=str(source["chunker_version"]),
            source_timestamp=datetime.fromisoformat(source["source_timestamp"]),
            is_current=bool(source["is_current"]),
            score=float(hit["_score"]),
        )
