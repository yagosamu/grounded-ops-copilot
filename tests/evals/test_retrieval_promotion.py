"""Production retrieval config follows the measured promotion thresholds."""

import json
from hashlib import sha256
from pathlib import Path

import pytest

from evals.retrieval.promotion import (
    CandidateMeasurement,
    RetrievalThresholds,
    select_candidate,
)
from modules.retrieval.config import load_retrieval_config

ROOT = Path(__file__).parents[2]


@pytest.mark.retrieval_eval
def test_selection_applies_retrieval_and_reranker_thresholds_literally() -> None:
    baseline = CandidateMeasurement("bm25", 1.0, 1.0, 26.0)
    candidates = (
        CandidateMeasurement("dense", 1.0, 1.0, 9.0),
        CandidateMeasurement("hybrid", 1.0, 1.0, 14.0),
        CandidateMeasurement("reranker", 1.0, 1.0, 20.0),
    )

    decision = select_candidate(
        baseline, candidates, RetrievalThresholds(0.05, 0.03, 400.0)
    )

    assert decision.production == "bm25"
    assert decision.fallback == "bm25"
    assert decision.relative_ndcg_deltas == {
        "dense": 0.0,
        "hybrid": 0.0,
        "reranker": 0.0,
    }
    assert decision.rejections == {
        "dense": "ndcg gain below 5.00%",
        "hybrid": "ndcg gain below 5.00%",
        "reranker": "ndcg gain below 3.00%",
    }


@pytest.mark.retrieval_eval
def test_versioned_config_names_bm25_production_fallback_and_immutable_reports() -> (
    None
):
    config = load_retrieval_config(ROOT / "config/retrieval.yaml")

    assert config.production_strategy == "bm25"
    assert config.fallback_strategy == "bm25"
    assert config.chunking_strategy == "structural"
    assert config.dataset_hash == (
        "64348837243fe876c3e024859bb4750bd25aa64a9f348994eb97f01443bd9dac"
    )
    for relative_path, expected_hash in config.reports.items():
        content = (ROOT / relative_path).read_bytes()
        assert sha256(content).hexdigest() == expected_hash


@pytest.mark.retrieval_eval
def test_candidate_report_records_measured_deltas_and_small_dataset_limitation() -> (
    None
):
    report = json.loads(
        (ROOT / "evals/retrieval/reports/candidates-v1.json").read_text(
            encoding="utf-8"
        )
    )

    assert report["dataset_hash"] == (
        "64348837243fe876c3e024859bb4750bd25aa64a9f348994eb97f01443bd9dac"
    )
    assert report["query_count"] == 2
    assert report["measurement_mode"] == "deterministic local fixture"
    assert [item["strategy"] for item in report["candidates"]] == [
        "dense",
        "hybrid",
        "reranker",
    ]
    assert all(item["ndcg_at_10"] == 1.0 for item in report["candidates"])
    assert all(item["relative_ndcg_delta"] == 0.0 for item in report["candidates"])
    assert all(item["query_p95_ms"] >= 0 for item in report["candidates"])
    assert all(item["qualified"] is False for item in report["candidates"])
    assert report["limitations"] == [
        "dataset v1 contains only 2 golden queries",
        "local deterministic embeddings do not represent provider embedding quality",
        "candidate latency is not comparable with the OpenSearch BM25 baseline",
    ]


@pytest.mark.retrieval_eval
def test_adr_links_reports_and_records_the_non_promotion_decision() -> None:
    adr = (ROOT / "docs/decisions/ADR-001-retrieval-strategy.md").read_text(
        encoding="utf-8"
    )

    assert "Production: `bm25` with `structural` chunking" in adr
    assert "Fallback: `bm25`" in adr
    assert "Dense: 0.00% relative nDCG@10 delta" in adr
    assert "Hybrid RRF: 0.00% relative nDCG@10 delta" in adr
    assert "Reranker: 0.00% relative nDCG@10 delta" in adr
    assert "../../evals/retrieval/reports/bm25-v1.json" in adr
    assert "../../evals/retrieval/reports/chunking-v1.json" in adr
    assert "../../evals/retrieval/reports/candidates-v1.json" in adr
