"""Measure retrieval candidates on the frozen public evaluation corpus."""

import json
import math
import re
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import yaml

from evals.retrieval.baseline import (
    BenchmarkConfig,
    CandidateResult,
    load_dataset,
    run_benchmark,
)
from evals.retrieval.promotion import (
    CandidateMeasurement,
    RetrievalThresholds,
    select_candidate,
)


@dataclass(frozen=True)
class CandidateReport:
    strategy: str
    config_hash: str
    recall_at_10: float
    mrr: float
    ndcg_at_10: float
    relative_ndcg_delta: float
    query_p50_ms: float
    query_p95_ms: float
    qualified: bool
    rejection: str


class OfflineCandidate:
    def __init__(self, strategy: str, sources: dict[str, str]) -> None:
        self.strategy = strategy
        self.sources = sources

    def search(self, question: str, limit: int) -> CandidateResult:
        started = perf_counter()
        lexical = self._lexical(question)
        dense = self._dense(question)
        if self.strategy == "dense":
            ranking = dense
        elif self.strategy == "hybrid":
            ranking = self._rrf(lexical, dense)
        else:
            hybrid = self._rrf(lexical, dense)
            ranking = tuple(
                sorted(
                    hybrid,
                    key=lambda source: (
                        -len(
                            _terms(question).intersection(_terms(self.sources[source]))
                        ),
                        hybrid.index(source),
                    ),
                )
            )
        return CandidateResult(ranking[:limit], (perf_counter() - started) * 1000)

    def _lexical(self, question: str) -> tuple[str, ...]:
        terms = _terms(question)
        return tuple(
            sorted(
                self.sources,
                key=lambda source: (
                    -len(terms.intersection(_terms(self.sources[source]))),
                    source,
                ),
            )
        )

    def _dense(self, question: str) -> tuple[str, ...]:
        query = _vector(question)
        return tuple(
            sorted(
                self.sources,
                key=lambda source: (
                    -_cosine(query, _vector(self.sources[source])),
                    source,
                ),
            )
        )

    @staticmethod
    def _rrf(lexical: tuple[str, ...], dense: tuple[str, ...]) -> tuple[str, ...]:
        scores = {
            source: sum(
                1 / (60 + ranking.index(source) + 1) for ranking in (lexical, dense)
            )
            for source in lexical
        }
        return tuple(sorted(scores, key=lambda source: (-scores[source], source)))


def run_candidate_experiment(
    manifest_path: Path, baseline_path: Path
) -> dict[str, object]:
    queries, dataset_hash = load_dataset(manifest_path)
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    sources = {
        item["id"]: (manifest_path.parent / item["fixture"]).read_text(encoding="utf-8")
        for item in raw["sources"]
    }
    baseline_raw = json.loads(baseline_path.read_text(encoding="utf-8"))
    baseline = CandidateMeasurement(
        "bm25",
        float(baseline_raw["recall_at_k"]),
        float(baseline_raw["ndcg_at_k"]),
        float(baseline_raw["latency_p95_ms"]),
    )
    reports = []
    measurements = []
    benchmark_reports = {}
    for strategy in ("dense", "hybrid", "reranker"):
        report = run_benchmark(
            queries,
            OfflineCandidate(strategy, sources),
            BenchmarkConfig(strategy, 10, "candidate-fixture-v1"),
            dataset_hash,
        )
        benchmark_reports[strategy] = report
        measurements.append(
            CandidateMeasurement(
                strategy, report.recall_at_k, report.ndcg_at_k, report.latency_p95_ms
            )
        )
    decision = select_candidate(
        baseline,
        tuple(measurements),
        RetrievalThresholds(0.05, 0.03, 400.0),
    )
    for measurement in measurements:
        report = benchmark_reports[measurement.strategy]
        reports.append(
            asdict(
                CandidateReport(
                    measurement.strategy,
                    report.config_hash,
                    report.recall_at_k,
                    report.mrr,
                    report.ndcg_at_k,
                    decision.relative_ndcg_deltas[measurement.strategy],
                    report.latency_p50_ms,
                    report.latency_p95_ms,
                    measurement.strategy == decision.production,
                    decision.rejections[measurement.strategy],
                )
            )
        )
    return {
        "dataset_hash": dataset_hash,
        "query_count": len(queries),
        "tenant_id": "public-eval",
        "policy": "public",
        "measurement_mode": "deterministic local fixture",
        "baseline": asdict(baseline),
        "candidates": reports,
        "production": decision.production,
        "fallback": decision.fallback,
        "limitations": [
            f"dataset v1 contains only {len(queries)} golden queries",
            (
                "local deterministic embeddings do not represent provider "
                "embedding quality"
            ),
            "candidate latency is not comparable with the OpenSearch BM25 baseline",
        ],
    }


def _terms(text: str) -> set[str]:
    return {
        token[:-1] if token.endswith("s") and len(token) > 4 else token
        for token in re.findall(r"[a-z0-9]+", text.lower())
    }


def _vector(text: str) -> tuple[float, ...]:
    values = [0.0] * 64
    for token in _terms(text):
        values[sha256(token.encode()).digest()[0] % len(values)] += 1.0
    length = math.sqrt(sum(value * value for value in values)) or 1.0
    return tuple(value / length for value in values)


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


if __name__ == "__main__":
    root = Path(__file__).parents[2]
    report = run_candidate_experiment(
        root / "evals/datasets/v1/manifest.yaml",
        root / "evals/retrieval/reports/bm25-v1.json",
    )
    (root / "evals/retrieval/reports/candidates-v1.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
