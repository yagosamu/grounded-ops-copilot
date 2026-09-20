"""Versioned vector projection and policy-filtered dense search."""

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, cast

from opensearchpy import OpenSearch

from modules.policy.enforcement import PolicyEnforcer


@dataclass(frozen=True)
class DenseDocument:
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
    corpus_version: str
    embedding_model: str
    embedding_version: str
    embedding: tuple[float, ...]


@dataclass(frozen=True)
class DenseSearchRequest:
    vector: tuple[float, ...]
    tenant_id: str
    access_policy: tuple[str, ...]
    corpus_version: str
    limit: int
    offset: int
    source_ids: tuple[str, ...] = ()
    document_ids: tuple[str, ...] = ()
    include_historical: bool = False


@dataclass(frozen=True)
class DenseSearchHit:
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
class DenseSearchResult:
    hits: tuple[DenseSearchHit, ...]
    total: int
    took_ms: int


class OpenSearchDenseAdapter:
    def __init__(
        self,
        client: OpenSearch,
        index: str,
        dimensions: int,
        enforcer: PolicyEnforcer,
    ) -> None:
        self.client = client
        self.index_name = index
        self.dimensions = dimensions
        self._enforcer = enforcer

    def ensure_index(self) -> None:
        if self.client.indices.exists(index=self.index_name):
            mapping = cast(
                dict[str, Any], self.client.indices.get_mapping(index=self.index_name)
            )
            dimension = mapping[self.index_name]["mappings"]["properties"]["embedding"][
                "dimension"
            ]
            if int(dimension) != self.dimensions:
                raise ValueError("dense index dimensions are immutable")
            return
        keyword_fields = (
            "chunk_id",
            "tenant_id",
            "source_id",
            "document_id",
            "document_version_id",
            "policy",
            "content_hash",
            "parser_version",
            "chunker_version",
            "corpus_version",
            "embedding_model",
            "embedding_version",
        )
        properties: dict[str, Any] = {
            field: {"type": "keyword"} for field in keyword_fields
        }
        properties.update(
            {
                "ordinal": {"type": "integer"},
                "text": {"type": "text"},
                "start": {"type": "integer"},
                "end": {"type": "integer"},
                "source_timestamp": {"type": "date"},
                "is_current": {"type": "boolean"},
                "embedding": {
                    "type": "knn_vector",
                    "dimension": self.dimensions,
                    "space_type": "cosinesimil",
                },
            }
        )
        self.client.indices.create(
            index=self.index_name,
            body={
                "settings": {"index.knn": True},
                "mappings": {"properties": properties},
            },
        )

    def index(self, documents: tuple[DenseDocument, ...]) -> None:
        self._enforcer.validate_projections(documents)
        body: list[dict[str, Any]] = []
        for document in documents:
            if len(document.embedding) != self.dimensions:
                raise ValueError("invalid embedding dimensions")
            payload = asdict(document)
            payload["source_timestamp"] = document.source_timestamp.isoformat()
            payload["embedding"] = list(document.embedding)
            payload["policy"] = list(document.policy)
            body.extend(
                (
                    {"index": {"_index": self.index_name, "_id": document.chunk_id}},
                    payload,
                )
            )
        response = cast(dict[str, Any], self.client.bulk(body=body, refresh="wait_for"))
        if response.get("errors"):
            raise RuntimeError("dense projection failed")

    def search(self, request: DenseSearchRequest) -> DenseSearchResult:
        filters: list[dict[str, Any]] = [
            {"term": {"tenant_id": request.tenant_id}},
            {"terms": {"policy": list(request.access_policy)}},
            {"term": {"corpus_version": request.corpus_version}},
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
                index=self.index_name,
                body={
                    "from": request.offset,
                    "size": request.limit,
                    "track_total_hits": True,
                    "query": {
                        "script_score": {
                            "query": {"bool": {"filter": filters}},
                            "script": {
                                "source": "knn_score",
                                "lang": "knn",
                                "params": {
                                    "field": "embedding",
                                    "query_value": list(request.vector),
                                    "space_type": "cosinesimil",
                                },
                            },
                        }
                    },
                    "sort": [{"_score": "desc"}, {"chunk_id": "asc"}],
                },
            ),
        )
        raw_total = response["hits"]["total"]
        total = raw_total["value"] if isinstance(raw_total, dict) else raw_total
        return DenseSearchResult(
            tuple(self._hit(item) for item in response["hits"]["hits"]),
            int(total),
            int(response["took"]),
        )

    @staticmethod
    def _hit(hit: dict[str, Any]) -> DenseSearchHit:
        source = hit["_source"]
        return DenseSearchHit(
            str(source["chunk_id"]),
            str(source["tenant_id"]),
            str(source["source_id"]),
            str(source["document_id"]),
            str(source["document_version_id"]),
            tuple(str(value) for value in source["policy"]),
            int(source["ordinal"]),
            str(source["text"]),
            int(source["start"]),
            int(source["end"]),
            str(source["content_hash"]),
            str(source["parser_version"]),
            str(source["chunker_version"]),
            datetime.fromisoformat(source["source_timestamp"]),
            bool(source["is_current"]),
            float(hit["_score"]),
        )
