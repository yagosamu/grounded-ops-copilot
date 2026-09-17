"""Create immutable versioned lexical indexes behind stable aliases."""

import json
from hashlib import sha256
from typing import Any, cast

from opensearchpy import OpenSearch


class IncompatibleIndexError(RuntimeError):
    """The requested physical index name already has a different schema."""


class LexicalIndexSchema:
    def __init__(self, client: OpenSearch, prefix: str = "groundedops-lexical") -> None:
        self.client = client
        self.prefix = prefix
        self.read_alias = f"{prefix}-read"
        self.write_alias = f"{prefix}-write"

    def index_name(self, schema_version: str) -> str:
        if not schema_version.isdigit() or len(schema_version) > 12:
            raise ValueError("invalid schema version")
        return f"{self.prefix}-v{schema_version}"

    def ensure(self, schema_version: str) -> str:
        index = self.index_name(schema_version)
        definition = self._definition(schema_version)
        fingerprint = self._fingerprint(definition)
        definition["mappings"]["_meta"] = {
            "schema_version": schema_version,
            "schema_fingerprint": fingerprint,
        }
        if self.client.indices.exists(index=index):
            existing = cast(
                dict[str, Any], self.client.indices.get_mapping(index=index)
            )
            actual = (
                existing.get(index, {})
                .get("mappings", {})
                .get("_meta", {})
                .get("schema_fingerprint")
            )
            if actual != fingerprint:
                raise IncompatibleIndexError(
                    "incompatible schema requires a new versioned index"
                )
            return index
        self.client.indices.create(index=index, body=definition)
        return index

    def _definition(self, schema_version: str) -> dict[str, Any]:
        return {
            "settings": {
                "analysis": {
                    "analyzer": {
                        "groundedops_text": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase"],
                        }
                    }
                }
            },
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "chunk_id": {"type": "keyword"},
                    "tenant_id": {"type": "keyword"},
                    "source_id": {"type": "keyword"},
                    "document_id": {"type": "keyword"},
                    "document_version_id": {"type": "keyword"},
                    "policy": {"type": "keyword"},
                    "ordinal": {"type": "integer"},
                    "text": {"type": "text", "analyzer": "groundedops_text"},
                    "start": {"type": "integer"},
                    "end": {"type": "integer"},
                    "content_hash": {"type": "keyword"},
                    "parser_version": {"type": "keyword"},
                    "chunker_version": {"type": "keyword"},
                    "source_timestamp": {"type": "date"},
                    "is_current": {"type": "boolean"},
                },
            },
            "aliases": {
                self.read_alias: {},
                self.write_alias: {"is_write_index": True},
            },
        }

    @staticmethod
    def _fingerprint(definition: dict[str, Any]) -> str:
        payload = json.dumps(definition, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()
