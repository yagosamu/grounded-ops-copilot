"""Deterministic metrics and report schema for the BM25 baseline."""

import json
import math
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Protocol

import yaml


@dataclass(frozen=True)
class BenchmarkConfig:
    strategy: str
    k: int
    index_schema: str


@dataclass(frozen=True)
class GoldenQuery:
    id: str
    question: str
    relevant_source_ids: tuple[str, ...]


@dataclass(frozen=True)
class CandidateResult:
    source_ids: tuple[str, ...]
    latency_ms: float


class RetrievalCandidate(Protocol):
    def search(self, question: str, limit: int) -> CandidateResult: ...


@dataclass(frozen=True)
class QueryReport:
    query_id: str
    retrieved_source_ids: tuple[str, ...]
    recall_at_k: float
    reciprocal_rank: float
    ndcg_at_k: float
    latency_ms: float | None
    error: str | None


@dataclass(frozen=True)
class BenchmarkReport:
    config_hash: str
    dataset_hash: str
    recall_at_k: float
    mrr: float
    ndcg_at_k: float
    latency_p50_ms: float
    latency_p95_ms: float
    errors: dict[str, str]
    queries: tuple[QueryReport, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


def load_dataset(manifest_path: Path) -> tuple[tuple[GoldenQuery, ...], str]:
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    questions = tuple(
        GoldenQuery(item["id"], item["question"], (item["evidence_source_id"],))
        for item in raw["golden_questions"]
    )
    digest = sha256()
    files = [manifest_path]
    files.extend(manifest_path.parent / source["fixture"] for source in raw["sources"])
    for path in sorted(files, key=lambda value: value.as_posix()):
        digest.update(path.relative_to(manifest_path.parent).as_posix().encode())
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return questions, digest.hexdigest()


def run_benchmark(
    queries: tuple[GoldenQuery, ...],
    candidate: RetrievalCandidate,
    config: BenchmarkConfig,
    dataset_hash: str,
) -> BenchmarkReport:
    if not queries:
        raise ValueError("benchmark requires queries")
    if config.k <= 0:
        raise ValueError("benchmark k must be positive")
    query_reports: list[QueryReport] = []
    errors: dict[str, str] = {}
    latencies: list[float] = []
    for query in queries:
        try:
            result = candidate.search(query.question, config.k)
            retrieved = result.source_ids[: config.k]
            recall, reciprocal_rank, ndcg = _metrics(
                retrieved, query.relevant_source_ids, config.k
            )
            latencies.append(result.latency_ms)
            query_reports.append(
                QueryReport(
                    query.id,
                    retrieved,
                    recall,
                    reciprocal_rank,
                    ndcg,
                    result.latency_ms,
                    None,
                )
            )
        except Exception:
            errors[query.id] = "retrieval_error"
            query_reports.append(
                QueryReport(query.id, (), 0.0, 0.0, 0.0, None, "retrieval_error")
            )
    size = len(query_reports)
    config_payload = json.dumps(
        asdict(config), sort_keys=True, separators=(",", ":")
    ).encode()
    return BenchmarkReport(
        sha256(config_payload).hexdigest(),
        dataset_hash,
        sum(item.recall_at_k for item in query_reports) / size,
        sum(item.reciprocal_rank for item in query_reports) / size,
        sum(item.ndcg_at_k for item in query_reports) / size,
        _percentile(latencies, 0.50),
        _percentile(latencies, 0.95),
        errors,
        tuple(query_reports),
    )


def _metrics(
    retrieved: tuple[str, ...], relevant: tuple[str, ...], k: int
) -> tuple[float, float, float]:
    relevant_set = set(relevant)
    if not relevant_set:
        return 0.0, 0.0, 0.0
    ranked = retrieved[:k]
    found = relevant_set.intersection(ranked)
    recall = len(found) / len(relevant_set)
    reciprocal_rank = next(
        (1 / rank for rank, value in enumerate(ranked, 1) if value in relevant_set),
        0.0,
    )
    dcg = sum(
        1 / math.log2(rank + 1)
        for rank, value in enumerate(ranked, 1)
        if value in relevant_set
    )
    ideal = sum(
        1 / math.log2(rank + 1) for rank in range(1, min(k, len(relevant_set)) + 1)
    )
    return recall, reciprocal_rank, dcg / ideal


def _percentile(values: list[float], quantile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)
