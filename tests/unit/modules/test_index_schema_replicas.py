"""Production index layout must match OpenSearch three-zone standby."""

import pytest
from opensearchpy import OpenSearch

from adapters.opensearch.index_schema import LexicalIndexSchema

pytestmark = pytest.mark.unit


def test_index_schema_uses_two_replicas_for_production() -> None:
    schema = LexicalIndexSchema(OpenSearch(hosts=["localhost"]), replicas=2)

    assert schema._definition("1")["settings"]["number_of_replicas"] == 2


def test_default_index_schema_retains_existing_fingerprint() -> None:
    schema = LexicalIndexSchema(OpenSearch(hosts=["localhost"]))

    assert "number_of_replicas" not in schema._definition("1")["settings"]


def test_index_schema_rejects_negative_replicas() -> None:
    with pytest.raises(ValueError, match="replicas must not be negative"):
        LexicalIndexSchema(OpenSearch(hosts=["localhost"]), replicas=-1)
