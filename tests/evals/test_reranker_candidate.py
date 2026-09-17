"""Reranker evaluation uses the frozen query set and reports quality."""

from pathlib import Path

import pytest

from evals.retrieval.baseline import (
    BenchmarkConfig,
    CandidateResult,
    load_dataset,
    run_benchmark,
)


class FrozenRerankerCandidate:
    def search(self, question: str, limit: int) -> CandidateResult:
        rankings = {
            "Which OpenTelemetry component provides access to tracers?": (
                "otel-tracing-api",
                "otel-resource",
            ),
            "What does an OpenTelemetry Resource represent?": (
                "otel-resource",
                "otel-tracing-api",
            ),
        }
        return CandidateResult(rankings[question][:limit], 20.0)


@pytest.mark.retrieval_eval
def test_reranker_candidate_uses_the_baseline_dataset_and_golden_queries() -> None:
    queries, dataset_hash = load_dataset(
        Path(__file__).parents[2] / "evals/datasets/v1/manifest.yaml"
    )

    report = run_benchmark(
        queries,
        FrozenRerankerCandidate(),
        BenchmarkConfig("hybrid+reranker-candidate", 10, "retrieval-v1"),
        dataset_hash,
    )

    assert report.dataset_hash == (
        "64348837243fe876c3e024859bb4750bd25aa64a9f348994eb97f01443bd9dac"
    )
    assert report.recall_at_k == 1.0
    assert report.ndcg_at_k == 1.0
