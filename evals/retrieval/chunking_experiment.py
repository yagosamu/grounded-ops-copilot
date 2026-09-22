"""Compare chunking strategies on one frozen corpus and query set."""

import json
import math
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from time import perf_counter

import yaml

from evals.retrieval.baseline import load_dataset
from modules.chunking.structural import StructuralChunker
from modules.parsing.parser import MarkdownParser


@dataclass(frozen=True)
class ChunkingExperimentConfig:
    fixed_characters: int
    fixed_overlap: int
    max_tokens: int

    def __post_init__(self) -> None:
        if (
            self.fixed_characters <= 0
            or self.fixed_overlap < 0
            or self.fixed_overlap >= self.fixed_characters
            or self.max_tokens <= 0
        ):
            raise ValueError("invalid chunking experiment configuration")


@dataclass(frozen=True)
class CandidateMetrics:
    strategy: str
    config_hash: str
    recall_at_10: float
    mrr: float
    ndcg_at_10: float
    citation_span_precision: float
    index_size_bytes: int
    ingest_ms: float
    query_p50_ms: float
    query_p95_ms: float


@dataclass(frozen=True)
class ChunkingExperimentReport:
    dataset_hash: str
    query_count: int
    candidates: tuple[CandidateMetrics, ...]
    limitations: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class _Source:
    identity: str
    text: str
    evidence_span: str


@dataclass(frozen=True)
class _Chunk:
    source_id: str
    text: str
    start: int
    end: int


def run_chunking_experiment(
    manifest_path: Path,
    config: ChunkingExperimentConfig,
    *,
    clock: Callable[[], float] = perf_counter,
) -> ChunkingExperimentReport:
    queries, dataset_hash = load_dataset(manifest_path)
    sources = _load_sources(manifest_path)
    strategies: tuple[
        tuple[str, Callable[[_Source, ChunkingExperimentConfig], tuple[_Chunk, ...]]],
        ...,
    ] = (
        ("fixed", _fixed),
        ("structural", _structural),
        ("parent-child", _parent_child),
    )
    candidates: list[CandidateMetrics] = []
    for name, chunker in strategies:
        started = clock()
        chunks = tuple(chunk for source in sources for chunk in chunker(source, config))
        ingest_ms = (clock() - started) * 1000
        recalls: list[float] = []
        reciprocal_ranks: list[float] = []
        ndcgs: list[float] = []
        citation_hits: list[float] = []
        query_latencies: list[float] = []
        for query in queries:
            query_started = clock()
            ranked_chunks = _rank(query.question, chunks)
            query_latencies.append((clock() - query_started) * 1000)
            source_ranking = tuple(
                dict.fromkeys(chunk.source_id for chunk in ranked_chunks)
            )
            relevant = query.relevant_source_ids[0]
            rank = next(
                (
                    index
                    for index, value in enumerate(source_ranking[:10], 1)
                    if value == relevant
                ),
                None,
            )
            recalls.append(float(rank is not None))
            reciprocal_ranks.append(0.0 if rank is None else 1 / rank)
            ndcgs.append(0.0 if rank is None else 1 / math.log2(rank + 1))
            relevant_chunk = next(
                (chunk for chunk in ranked_chunks if chunk.source_id == relevant), None
            )
            source = next(item for item in sources if item.identity == relevant)
            citation_hits.append(
                float(
                    relevant_chunk is not None
                    and source.evidence_span in relevant_chunk.text
                )
            )
        size = len(queries)
        config_hash = _config_hash(name, config)
        candidates.append(
            CandidateMetrics(
                name,
                config_hash,
                sum(recalls) / size,
                sum(reciprocal_ranks) / size,
                sum(ndcgs) / size,
                sum(citation_hits) / size,
                len(
                    json.dumps(
                        [asdict(chunk) for chunk in chunks],
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode()
                ),
                ingest_ms,
                _percentile(query_latencies, 0.50),
                _percentile(query_latencies, 0.95),
            )
        )
    return ChunkingExperimentReport(
        dataset_hash,
        len(queries),
        tuple(candidates),
        (f"dataset v1 contains only {len(queries)} golden queries",),
    )


def _load_sources(manifest_path: Path) -> tuple[_Source, ...]:
    raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    return tuple(
        _Source(
            item["id"],
            (manifest_path.parent / item["fixture"]).read_text(encoding="utf-8"),
            item["evidence_span"],
        )
        for item in raw["sources"]
    )


def _fixed(source: _Source, config: ChunkingExperimentConfig) -> tuple[_Chunk, ...]:
    step = config.fixed_characters - config.fixed_overlap
    return tuple(
        _Chunk(
            source.identity,
            source.text[start : start + config.fixed_characters],
            start,
            min(len(source.text), start + config.fixed_characters),
        )
        for start in range(0, len(source.text), step)
    )


def _structural(
    source: _Source, config: ChunkingExperimentConfig
) -> tuple[_Chunk, ...]:
    document = MarkdownParser().parse(source.text.encode())
    return tuple(
        _Chunk(source.identity, chunk.text, chunk.start, chunk.end)
        for chunk in StructuralChunker(config.max_tokens).chunk(document)
    )


def _parent_child(
    source: _Source, config: ChunkingExperimentConfig
) -> tuple[_Chunk, ...]:
    document = MarkdownParser().parse(source.text.encode())
    chunks: list[_Chunk] = []
    for section in document.sections:
        heading_end = section.text.find("\n") + 1
        heading = section.text[:heading_end].strip()
        body_start = section.start + heading_end
        for sentence in re.finditer(r"[^.!?\n]+[.!?]?", section.text[heading_end:]):
            if not sentence.group().strip():
                continue
            start = body_start + sentence.start()
            end = body_start + sentence.end()
            chunks.append(
                _Chunk(
                    source.identity,
                    f"{heading}\n{source.text[start:end].strip()}",
                    start,
                    end,
                )
            )
    return tuple(chunks)


def _rank(question: str, chunks: tuple[_Chunk, ...]) -> tuple[_Chunk, ...]:
    query_terms = _terms(question)
    return tuple(
        sorted(
            chunks,
            key=lambda chunk: (
                -len(query_terms.intersection(_terms(chunk.text))),
                chunk.source_id,
                chunk.start,
            ),
        )
    )


def _terms(text: str) -> set[str]:
    terms = set()
    for raw in re.findall(r"[a-z0-9]+", text.lower()):
        terms.add(raw[:-1] if raw.endswith("s") and len(raw) > 4 else raw)
    return terms


def _config_hash(strategy: str, config: ChunkingExperimentConfig) -> str:
    payload = json.dumps(
        {"strategy": strategy, "config": asdict(config)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return sha256(payload).hexdigest()


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


if __name__ == "__main__":
    root = Path(__file__).parents[2]
    output = root / "evals/retrieval/reports/chunking-v1.json"
    result = run_chunking_experiment(
        root / "evals/datasets/v1/manifest.yaml",
        ChunkingExperimentConfig(96, 16, 80),
    )
    output.write_text(result.to_json(), encoding="utf-8", newline="\n")
