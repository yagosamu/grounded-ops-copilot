"""Blue-green rebuilds keep the live retrieval alias on validated evidence."""

import json
import logging
from collections.abc import Iterator
from uuid import uuid4

import pytest
from opensearchpy import OpenSearch

from adapters.opensearch.index_schema import IndexAliasError, LexicalIndexSchema
from modules.indexing.rebuild import IndexRebuilder, IndexValidationError

pytestmark = [pytest.mark.integration, pytest.mark.operational]


@pytest.fixture
def live_index(
    opensearch_client: OpenSearch,
) -> Iterator[tuple[LexicalIndexSchema, str]]:
    prefix = f"test-rebuild-{uuid4().hex}"
    schema = LexicalIndexSchema(opensearch_client, prefix)
    previous = schema.ensure("1")
    opensearch_client.index(
        index=schema.write_alias,
        id="old",
        body={"text": "previous validated evidence"},
        refresh=True,
    )
    yield schema, previous
    opensearch_client.indices.delete(index=f"{prefix}-*", ignore_unavailable=True)


def visible_ids(client: OpenSearch, alias: str) -> list[str]:
    response = client.search(index=alias, body={"query": {"match_all": {}}, "size": 10})
    return [hit["_id"] for hit in response["hits"]["hits"]]


def test_validated_rebuild_atomically_promotes_read_and_write_aliases(
    live_index: tuple[LexicalIndexSchema, str],
    opensearch_client: OpenSearch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema, previous = live_index
    rebuilder = IndexRebuilder(schema)

    def build(candidate: str) -> None:
        assert visible_ids(opensearch_client, schema.read_alias) == ["old"]
        opensearch_client.index(
            index=candidate,
            id="new",
            body={"text": "new validated evidence"},
            refresh=True,
        )

    def validate(candidate: str) -> bool:
        return opensearch_client.count(index=candidate)["count"] == 1

    with caplog.at_level(logging.INFO, logger="grounded_ops.telemetry"):
        switch = rebuilder.rebuild("2", build=build, validate=validate)

    assert switch.previous_index == previous
    assert switch.current_index == schema.index_name("2")
    assert visible_ids(opensearch_client, schema.read_alias) == ["new"]
    assert visible_ids(opensearch_client, previous) == ["old"]
    success = json.loads(caplog.records[-1].message)
    assert success["operation"] == "index.rebuild"
    assert success["outcome"] == "success"
    assert success["entry_point"] == "cli"
    assert success["duration_ms"] >= 0
    opensearch_client.index(
        index=schema.write_alias,
        id="post-switch",
        body={"text": "written after promotion"},
        refresh=True,
    )
    assert set(visible_ids(opensearch_client, switch.current_index)) == {
        "new",
        "post-switch",
    }
    assert visible_ids(opensearch_client, previous) == ["old"]


def test_failed_validation_keeps_both_aliases_on_previous_index(
    live_index: tuple[LexicalIndexSchema, str],
    opensearch_client: OpenSearch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema, previous = live_index
    rebuilder = IndexRebuilder(schema)

    def build(candidate: str) -> None:
        opensearch_client.index(
            index=candidate,
            id="unverified",
            body={"text": "not yet validated"},
            refresh=True,
        )

    with caplog.at_level(logging.ERROR, logger="grounded_ops.telemetry"):
        with pytest.raises(IndexValidationError, match="validation failed"):
            rebuilder.rebuild("2", build=build, validate=lambda candidate: False)

    assert visible_ids(opensearch_client, schema.read_alias) == ["old"]
    opensearch_client.index(
        index=schema.write_alias,
        id="still-old",
        body={"text": "write stays on old index"},
        refresh=True,
    )
    assert set(visible_ids(opensearch_client, previous)) == {"old", "still-old"}
    assert visible_ids(opensearch_client, schema.index_name("2")) == ["unverified"]
    failure = json.loads(caplog.records[-1].message)
    assert failure["operation"] == "index.rebuild"
    assert failure["outcome"] == "error"
    assert failure["error_type"] == "IndexValidationError"
    assert "not yet validated" not in caplog.text


def test_interrupted_build_never_exposes_partial_candidate(
    live_index: tuple[LexicalIndexSchema, str], opensearch_client: OpenSearch
) -> None:
    schema, previous = live_index

    def interrupted_build(candidate: str) -> None:
        opensearch_client.index(
            index=candidate,
            id="partial",
            body={"text": "incomplete corpus"},
            refresh=True,
        )
        raise RuntimeError("simulated interruption")

    with pytest.raises(RuntimeError, match="simulated interruption"):
        IndexRebuilder(schema).rebuild(
            "2", build=interrupted_build, validate=lambda candidate: True
        )

    assert visible_ids(opensearch_client, schema.read_alias) == ["old"]
    assert schema.active_index() == previous
    assert visible_ids(opensearch_client, schema.index_name("2")) == ["partial"]


def test_rollback_restores_both_aliases_without_deleting_the_new_index(
    live_index: tuple[LexicalIndexSchema, str],
    opensearch_client: OpenSearch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema, previous = live_index
    rebuilder = IndexRebuilder(schema)

    def build(candidate: str) -> None:
        opensearch_client.index(
            index=candidate,
            id="new",
            body={"text": "new evidence"},
            refresh=True,
        )

    switch = rebuilder.rebuild("2", build=build, validate=lambda candidate: True)
    with caplog.at_level(logging.INFO, logger="grounded_ops.telemetry"):
        rebuilder.rollback(switch)

    assert schema.active_index() == previous
    assert visible_ids(opensearch_client, schema.read_alias) == ["old"]
    assert visible_ids(opensearch_client, switch.current_index) == ["new"]
    opensearch_client.index(
        index=schema.write_alias,
        id="after-rollback",
        body={"text": "old index receives writes again"},
        refresh=True,
    )
    assert set(visible_ids(opensearch_client, previous)) == {"old", "after-rollback"}
    rollback = json.loads(caplog.records[-1].message)
    assert rollback["operation"] == "index.rollback"
    assert rollback["outcome"] == "success"


def test_stale_rollback_cannot_overwrite_a_newer_promotion(
    live_index: tuple[LexicalIndexSchema, str], opensearch_client: OpenSearch
) -> None:
    schema, _ = live_index
    rebuilder = IndexRebuilder(schema)

    def build(candidate: str) -> None:
        opensearch_client.index(
            index=candidate,
            id=candidate,
            body={"text": "validated evidence"},
            refresh=True,
        )

    first = rebuilder.rebuild("2", build=build, validate=lambda candidate: True)
    second = rebuilder.rebuild("3", build=build, validate=lambda candidate: True)

    with pytest.raises(IndexAliasError, match="active index changed"):
        rebuilder.rollback(first)

    assert schema.active_index() == second.current_index
    assert visible_ids(opensearch_client, schema.read_alias) == [second.current_index]


def test_lost_alias_acknowledgment_compensates_to_previous_index(
    live_index: tuple[LexicalIndexSchema, str],
    opensearch_client: OpenSearch,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    schema, previous = live_index
    update_aliases = opensearch_client.indices.update_aliases
    first_call = True

    def lose_first_acknowledgment(*args: object, **kwargs: object) -> object:
        nonlocal first_call
        response = update_aliases(*args, **kwargs)
        if first_call:
            first_call = False
            raise ConnectionError("acknowledgment lost after alias switch")
        return response

    monkeypatch.setattr(
        opensearch_client.indices, "update_aliases", lose_first_acknowledgment
    )

    def build(candidate: str) -> None:
        opensearch_client.index(
            index=candidate,
            id="new",
            body={"text": "validated evidence"},
            refresh=True,
        )

    with caplog.at_level(logging.INFO, logger="grounded_ops.telemetry"):
        with pytest.raises(ConnectionError, match="acknowledgment lost"):
            IndexRebuilder(schema).rebuild(
                "2", build=build, validate=lambda candidate: True
            )

    assert schema.active_index() == previous
    assert visible_ids(opensearch_client, schema.read_alias) == ["old"]
    events = [json.loads(record.message) for record in caplog.records]
    assert [(event["operation"], event["outcome"]) for event in events] == [
        ("index.rollback", "success"),
        ("index.rebuild", "error"),
    ]
    assert events[0]["correlation_id"] == events[1]["correlation_id"]
