"""Versioned lexical indexes are immutable OpenSearch projections."""

from collections.abc import Iterator
from uuid import uuid4

import pytest
from opensearchpy import OpenSearch

from adapters.opensearch.index_schema import IncompatibleIndexError, LexicalIndexSchema


@pytest.fixture
def schema(opensearch_client: OpenSearch) -> Iterator[LexicalIndexSchema]:
    prefix = f"test-lexical-{uuid4().hex}"
    value = LexicalIndexSchema(opensearch_client, prefix)
    yield value
    opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)


@pytest.mark.integration
def test_creates_versioned_index_with_policy_fields_and_aliases(
    schema: LexicalIndexSchema, opensearch_client: OpenSearch
) -> None:
    index = schema.ensure("1")

    mapping = opensearch_client.indices.get_mapping(index=index)[index]["mappings"]
    aliases = opensearch_client.indices.get_alias(index=index)[index]["aliases"]
    assert index.endswith("-v1")
    assert mapping["properties"]["text"]["analyzer"] == "groundedops_text"
    assert mapping["properties"]["tenant_id"]["type"] == "keyword"
    assert mapping["properties"]["policy"]["type"] == "keyword"
    assert mapping["properties"]["is_current"]["type"] == "boolean"
    assert aliases[schema.read_alias] == {}
    assert aliases[schema.write_alias]["is_write_index"] is True


@pytest.mark.integration
def test_creation_is_idempotent(schema: LexicalIndexSchema) -> None:
    first = schema.ensure("1")
    second = schema.ensure("1")

    assert second == first


@pytest.mark.integration
def test_rejects_incompatible_in_place_schema(
    schema: LexicalIndexSchema, opensearch_client: OpenSearch
) -> None:
    index = schema.index_name("1")
    opensearch_client.indices.create(
        index=index,
        body={"mappings": {"_meta": {"schema_fingerprint": "different"}}},
    )

    with pytest.raises(IncompatibleIndexError, match="new versioned index"):
        schema.ensure("1")
