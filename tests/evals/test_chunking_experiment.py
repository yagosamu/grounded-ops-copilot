"""Chunking experiments are reproducible and report every promotion metric."""

import json
from pathlib import Path

import pytest

from evals.retrieval.chunking_experiment import (
    ChunkingExperimentConfig,
    run_chunking_experiment,
)


class StepClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.001
        return self.value


MANIFEST = Path(__file__).parents[2] / "evals/datasets/v1/manifest.yaml"


@pytest.mark.retrieval_eval
def test_compares_all_chunkers_on_one_frozen_dataset_with_complete_metrics() -> None:
    report = run_chunking_experiment(
        MANIFEST,
        ChunkingExperimentConfig(fixed_characters=96, fixed_overlap=16, max_tokens=80),
        clock=StepClock(),
    )

    assert report.dataset_hash == (
        "64348837243fe876c3e024859bb4750bd25aa64a9f348994eb97f01443bd9dac"
    )
    assert report.query_count == 2
    assert [candidate.strategy for candidate in report.candidates] == [
        "fixed",
        "structural",
        "parent-child",
    ]
    for candidate in report.candidates:
        assert len(candidate.config_hash) == 64
        assert candidate.recall_at_10 == 1.0
        assert candidate.mrr == 1.0
        assert candidate.ndcg_at_10 == 1.0
        assert candidate.citation_span_precision == 1.0
        assert candidate.index_size_bytes > 0
        assert candidate.ingest_ms == pytest.approx(1.0)
        assert candidate.query_p50_ms == pytest.approx(1.0)
        assert candidate.query_p95_ms == pytest.approx(1.0)
    assert report.limitations == ("dataset v1 contains only 2 golden queries",)


@pytest.mark.retrieval_eval
def test_quality_and_hashes_repeat_for_identical_inputs() -> None:
    config = ChunkingExperimentConfig(96, 16, 80)

    first = run_chunking_experiment(MANIFEST, config, clock=StepClock())
    second = run_chunking_experiment(MANIFEST, config, clock=StepClock())

    assert first == second
    assert first.to_json() == second.to_json()


@pytest.mark.retrieval_eval
def test_persisted_chunking_report_matches_the_frozen_dataset() -> None:
    report = json.loads(
        (
            Path(__file__).parents[2] / "evals/retrieval/reports/chunking-v1.json"
        ).read_text(encoding="utf-8")
    )

    assert report["dataset_hash"] == (
        "64348837243fe876c3e024859bb4750bd25aa64a9f348994eb97f01443bd9dac"
    )
    assert report["query_count"] == 2
    assert [item["strategy"] for item in report["candidates"]] == [
        "fixed",
        "structural",
        "parent-child",
    ]
    assert all(item["index_size_bytes"] > 0 for item in report["candidates"])
    assert all(item["ingest_ms"] >= 0 for item in report["candidates"])
    assert all(item["query_p95_ms"] >= 0 for item in report["candidates"])
