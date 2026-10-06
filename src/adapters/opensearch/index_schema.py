"""Create immutable versioned lexical indexes behind stable aliases."""

import json
from hashlib import sha256
from typing import Any, cast

from opensearchpy import OpenSearch


class IncompatibleIndexError(RuntimeError):
    """The requested physical index name already has a different schema."""


class IndexAliasError(RuntimeError):
    """The read and write aliases cannot safely be moved."""


class LexicalIndexSchema:
    def __init__(
        self, client: OpenSearch, prefix: str = "groundedops-lexical", replicas: int = 1
    ) -> None:
        if replicas < 0:
            raise ValueError("replicas must not be negative")
        self.client = client
        self.prefix = prefix
        self.replicas = replicas
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

    def create_candidate(self, schema_version: str) -> str:
        index = self.index_name(schema_version)
        if self.client.indices.exists(index=index):
            raise IncompatibleIndexError("candidate index already exists")
        definition = self._definition(schema_version)
        definition["mappings"]["_meta"] = {
            "schema_version": schema_version,
            "schema_fingerprint": self._fingerprint(definition),
        }
        del definition["aliases"]
        self.client.indices.create(index=index, body=definition)
        return index

    def active_index(self) -> str:
        read = cast(dict[str, Any], self.client.indices.get_alias(name=self.read_alias))
        write = cast(
            dict[str, Any], self.client.indices.get_alias(name=self.write_alias)
        )
        if len(read) != 1 or set(read) != set(write):
            raise IndexAliasError("read and write aliases disagree")
        index = next(iter(read))
        if write[index]["aliases"][self.write_alias].get("is_write_index") is not True:
            raise IndexAliasError("write alias has no active index")
        return index

    def switch_aliases(self, expected_index: str, target_index: str) -> None:
        if self.active_index() != expected_index:
            raise IndexAliasError("active index changed during rebuild")
        if not self.client.indices.exists(index=target_index):
            raise IndexAliasError("target index is unavailable")
        response = cast(
            dict[str, Any],
            self.client.indices.update_aliases(
                body={
                    "actions": [
                        {
                            "remove": {
                                "index": expected_index,
                                "alias": self.read_alias,
                                "must_exist": True,
                            }
                        },
                        {
                            "remove": {
                                "index": expected_index,
                                "alias": self.write_alias,
                                "must_exist": True,
                            }
                        },
                        {"add": {"index": target_index, "alias": self.read_alias}},
                        {
                            "add": {
                                "index": target_index,
                                "alias": self.write_alias,
                                "is_write_index": True,
                            }
                        },
                    ]
                }
            ),
        )
        if not response.get("acknowledged"):
            raise IndexAliasError("alias switch was not acknowledged")
        if self.active_index() != target_index:
            raise IndexAliasError("alias switch did not converge")

    def _definition(self, schema_version: str) -> dict[str, Any]:
        definition: dict[str, Any] = {
            "settings": {
                "analysis": {
                    "analyzer": {
                        "groundedops_text": {
                            "type": "custom",
                            "tokenizer": "standard",
                            "filter": ["lowercase"],
                        }
                    }
                },
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
        if self.replicas != 1:
            definition["settings"]["number_of_replicas"] = self.replicas
        return definition

    @staticmethod
    def _fingerprint(definition: dict[str, Any]) -> str:
        payload = json.dumps(definition, sort_keys=True, separators=(",", ":"))
        return sha256(payload.encode()).hexdigest()
