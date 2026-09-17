"""The BM25 baseline report is deterministic and computes standard IR metrics."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from evals.retrieval.baseline import (
    BenchmarkConfig,
    CandidateResult,
    GoldenQuery,
    load_dataset,
    run_benchmark,
)


class FrozenCandidate:
    def __init__(self, results: dict[str, CandidateResult | Exception]) -> None:
        self.results = results

    def search(self, question: str, limit: int) -> CandidateResult:
        result = self.results[question]
        if isinstance(result, Exception):
            raise result
        return replace(result, source_ids=result.source_ids[:limit])


QUERIES = (
    GoldenQuery("q1", "first", ("source-a",)),
    GoldenQuery("q2", "second", ("source-b",)),
    GoldenQuery("q3", "failure", ("source-c",)),
)


@pytest.mark.retrieval_eval
def test_computes_recall_mrr_ndcg_latency_and_errors() -> None:
    candidate = FrozenCandidate(
        {
            "first": CandidateResult(("source-a", "source-x"), 10.0),
            "second": CandidateResult(("source-x", "source-b"), 30.0),
            "failure": RuntimeError("dependency unavailable"),
        }
    )

    report = run_benchmark(
        QUERIES,
        candidate,
        BenchmarkConfig("bm25", 2, "schema-v1"),
        dataset_hash="dataset-hash",
    )

    assert report.recall_at_k == pytest.approx(2 / 3)
    assert report.mrr == pytest.approx(0.5)
    assert report.ndcg_at_k == pytest.approx((1 + 1 / 1.584962500721156) / 3)
    assert report.latency_p50_ms == 20.0
    assert report.latency_p95_ms == 29.0
    assert report.errors == {"q3": "retrieval_error"}
    assert report.queries[0].retrieved_source_ids == ("source-a", "source-x")
    assert report.queries[1].reciprocal_rank == 0.5
    assert report.queries[2].error == "retrieval_error"


@pytest.mark.retrieval_eval
def test_report_and_hashes_are_deterministic() -> None:
    candidate = FrozenCandidate(
        {
            "first": CandidateResult(("source-a",), 10.0),
            "second": CandidateResult(("source-b",), 20.0),
            "failure": CandidateResult((), 30.0),
        }
    )
    config = BenchmarkConfig("bm25", 10, "schema-v1")

    first = run_benchmark(QUERIES, candidate, config, "fixed-dataset-hash")
    second = run_benchmark(QUERIES, candidate, config, "fixed-dataset-hash")

    assert first == second
    assert (
        first.config_hash
        == "7381ddf595c4e744226d356729ea29918613313bc96317bfdffaf9c683878927"
    )
    assert first.dataset_hash == "fixed-dataset-hash"
    assert first.to_json() == second.to_json()


@pytest.mark.retrieval_eval
def test_persisted_v1_report_matches_the_versioned_dataset() -> None:
    root = Path(__file__).parents[2]
    _, dataset_hash = load_dataset(root / "evals/datasets/v1/manifest.yaml")
    report = json.loads(
        (root / "evals/retrieval/reports/bm25-v1.json").read_text(encoding="utf-8")
    )

    assert report["dataset_hash"] == dataset_hash
    assert report["config_hash"] == (
        "7381ddf595c4e744226d356729ea29918613313bc96317bfdffaf9c683878927"
    )
    assert report["recall_at_k"] == 1.0
    assert report["mrr"] == 1.0
    assert report["ndcg_at_k"] == 1.0
    assert report["latency_p50_ms"] == 17.0
    assert report["latency_p95_ms"] == 26.0
    assert report["errors"] == {}
    assert [query["query_id"] for query in report["queries"]] == [
        "tracing-components",
        "resource-entity",
    ]
