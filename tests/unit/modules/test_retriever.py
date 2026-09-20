"""BM25 retrieval returns only typed, reauthorized evidence."""

from datetime import UTC, datetime

import pytest

from adapters.opensearch.search import SearchHit, SearchRequest, SearchResult
from adapters.policy.snapshot import SnapshotPolicyStore
from modules.policy.authorizer import Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.retriever import (
    BM25Retriever,
    QueryContext,
    RetrievalUnavailable,
)


class FakeSearchAdapter:
    def __init__(self, result: SearchResult | Exception) -> None:
        self.result = result
        self.request: SearchRequest | None = None

    def search(self, request: SearchRequest) -> SearchResult:
        self.request = request
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


class FailingPolicyStore:
    def get_document_policies(self, tenant_id, references):
        raise RuntimeError("postgres://admin:private@metadata")


def hit(
    chunk_id: str,
    score: float,
    tenant_id: str = "alpha",
    policy: tuple[str, ...] = ("role:engineer",),
) -> SearchHit:
    return SearchHit(
        chunk_id=chunk_id,
        tenant_id=tenant_id,
        source_id="source-1",
        document_id=f"document-{chunk_id}",
        document_version_id=f"version-{chunk_id}",
        policy=policy,
        ordinal=0,
        text=f"evidence {chunk_id}",
        start=10,
        end=20,
        content_hash=f"hash-{chunk_id}",
        parser_version="markdown-v1",
        chunker_version="structural-v1",
        source_timestamp=datetime(2026, 9, 17, tzinfo=UTC),
        is_current=True,
        score=score,
    )


def enforcer(*hits: SearchHit) -> PolicyEnforcer:
    return PolicyEnforcer(
        SnapshotPolicyStore(
            tuple(
                DocumentPolicy(
                    item.tenant_id,
                    item.source_id,
                    item.document_id,
                    item.document_version_id,
                    item.policy,
                    False,
                )
                for item in hits
            )
        )
    )


@pytest.mark.unit
def test_returns_ranked_evidence_with_version_and_provenance() -> None:
    adapter = FakeSearchAdapter(
        SearchResult((hit("best", 4.2), hit("next", 2.1)), 2, 7)
    )
    retriever = BM25Retriever(adapter, enforcer(*adapter.result.hits))

    result = retriever.retrieve(
        QueryContext("tracer provider", Principal("alice", "alpha", ("engineer",), ()))
    )

    assert [item.chunk_id for item in result.evidence] == ["best", "next"]
    assert result.evidence[0].score == 4.2
    assert result.evidence[0].document_version_id == "version-best"
    assert result.evidence[0].span == (10, 20)
    assert result.evidence[0].source_timestamp == datetime(2026, 9, 17, tzinfo=UTC)
    assert result.evidence[0].is_current is True
    assert result.strategy == "bm25"
    assert result.total == 2
    assert result.took_ms == 7


@pytest.mark.unit
def test_removes_cross_tenant_and_document_denied_hits() -> None:
    adapter = FakeSearchAdapter(
        SearchResult(
            (
                hit("allowed", 3.0),
                hit("cross", 9.0, tenant_id="beta"),
                hit("denied", 8.0, policy=("role:finance",)),
            ),
            3,
            2,
        )
    )
    assert isinstance(adapter.result, SearchResult)
    retriever = BM25Retriever(adapter, enforcer(*adapter.result.hits))

    result = retriever.retrieve(
        QueryContext("evidence", Principal("alice", "alpha", ("engineer",), ()))
    )

    assert [item.chunk_id for item in result.evidence] == ["allowed"]


@pytest.mark.unit
def test_preserves_empty_results_and_diagnostics() -> None:
    retriever = BM25Retriever(FakeSearchAdapter(SearchResult((), 0, 3)), enforcer())

    result = retriever.retrieve(
        QueryContext("missing", Principal("alice", "alpha", ("engineer",), ()))
    )

    assert result.evidence == ()
    assert result.total == 0
    assert result.took_ms == 3


@pytest.mark.unit
def test_dependency_failure_is_typed_and_redacted() -> None:
    retriever = BM25Retriever(
        FakeSearchAdapter(RuntimeError("http://admin:private@search:9200")),
        enforcer(),
    )

    with pytest.raises(RetrievalUnavailable, match="retrieval unavailable") as error:
        retriever.retrieve(
            QueryContext("query", Principal("alice", "alpha", ("engineer",), ()))
        )

    assert "private" not in str(error.value)


@pytest.mark.unit
def test_authoritative_policy_failure_is_typed_and_redacted() -> None:
    retriever = BM25Retriever(
        FakeSearchAdapter(SearchResult((hit("candidate", 1.0),), 1, 2)),
        PolicyEnforcer(FailingPolicyStore()),
    )

    with pytest.raises(RetrievalUnavailable, match="retrieval unavailable") as error:
        retriever.retrieve(
            QueryContext("query", Principal("alice", "alpha", ("engineer",), ()))
        )

    assert "private" not in str(error.value)
