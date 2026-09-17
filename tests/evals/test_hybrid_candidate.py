"""The hybrid candidate uses the frozen retrieval benchmark."""

from pathlib import Path

import pytest

from evals.retrieval.baseline import (
    BenchmarkConfig,
    CandidateResult,
    load_dataset,
    run_benchmark,
)


class FrozenHybridCandidate:
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
        return CandidateResult(rankings[question][:limit], 14.0)


@pytest.mark.retrieval_eval
def test_hybrid_candidate_uses_the_baseline_dataset_and_golden_queries() -> None:
    queries, dataset_hash = load_dataset(
        Path(__file__).parents[2] / "evals/datasets/v1/manifest.yaml"
    )

    report = run_benchmark(
        queries,
        FrozenHybridCandidate(),
        BenchmarkConfig("hybrid-rrf-candidate", 10, "lexical-v1+dense-v1"),
        dataset_hash,
    )

    assert report.dataset_hash == (
        "64348837243fe876c3e024859bb4750bd25aa64a9f348994eb97f01443bd9dac"
    )
    assert report.recall_at_k == 1.0
    assert report.ndcg_at_k == 1.0
