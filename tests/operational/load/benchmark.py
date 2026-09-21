"""Reproducible local capacity baseline for core Search, Ask and ingestion paths.

This is a component benchmark, not a production SLO test. External dependencies are
replaced by deterministic in-process adapters so the report isolates application
orchestration. The Pilot environment must repeat the workload with real services.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
from collections import Counter
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Any

from adapters.opensearch.search import SearchHit, SearchRequest, SearchResult
from adapters.policy.snapshot import SnapshotPolicyStore
from domain.answering import Question
from interfaces.http.ask import AskService
from modules.answering.abstention import AbstentionDecider
from modules.answering.context_packer import ContextPacker
from modules.answering.generator import (
    GenerationCompleted,
    GenerationRequest,
    GenerationResponse,
    ProposedCitation,
    ProposedClaim,
)
from modules.answering.verifier import CitationVerifier
from modules.chunking.structural import StructuralChunker
from modules.parsing.parser import MarkdownParser
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.retriever import (
    BM25Retriever,
    Evidence,
    EvidenceSet,
    QueryContext,
)

SCHEMA_VERSION = "capacity-report.v1"
REQUIRED_WORKLOADS = frozenset({"search", "ask", "ingestion"})
CORPUS = b"""# Recovery runbook

Restart the worker only after draining its queue. Confirm the health check before
restoring traffic and record the deployment identifier in the incident timeline.

## Diagnosis

Compare the error rate with the latest deployment and inspect queue depth. A growing
queue with healthy dependencies indicates worker saturation.

## Rollback

Rollback to the previous release when the error rate remains elevated for ten minutes.
"""


@dataclass(frozen=True)
class HardwareProfile:
    operating_system: str
    architecture: str
    cpu_model: str
    logical_cpus: int
    memory_gb: float
    python_version: str


@dataclass(frozen=True)
class DatasetProfile:
    name: str
    version: str
    sha256: str
    documents: int
    chunks: int
    questions: int
    bytes: int


@dataclass(frozen=True)
class BenchmarkPlan:
    concurrency: tuple[int, ...]
    warmup_iterations: int
    measured_iterations: int
    request_timeout_seconds: float
    saturation_gain_threshold: float

    def __post_init__(self) -> None:
        if (
            not self.concurrency
            or tuple(sorted(set(self.concurrency))) != self.concurrency
            or any(value <= 0 for value in self.concurrency)
            or self.warmup_iterations <= 0
            or self.measured_iterations <= 0
            or self.request_timeout_seconds <= 0
            or not 0 < self.saturation_gain_threshold < 1
        ):
            raise ValueError("invalid benchmark plan")


@dataclass(frozen=True)
class ScenarioMeasurement:
    concurrency: int
    requests: int
    successes: int
    errors: int
    error_rate: float
    duration_seconds: float
    throughput_rps: float
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    max_in_flight: int
    error_categories: dict[str, int]

    @classmethod
    def sample(cls, *, concurrency: int, throughput_rps: float) -> ScenarioMeasurement:
        """Build a valid synthetic point for saturation algorithm self-checks."""
        return cls(
            concurrency=concurrency,
            requests=1,
            successes=1,
            errors=0,
            error_rate=0.0,
            duration_seconds=1.0,
            throughput_rps=throughput_rps,
            latency_p50_ms=1.0,
            latency_p95_ms=1.0,
            latency_p99_ms=1.0,
            max_in_flight=1,
            error_categories={},
        )


@dataclass(frozen=True)
class WorkloadReport:
    measurements: tuple[ScenarioMeasurement, ...]
    saturation_concurrency: int | None


@dataclass(frozen=True)
class CapacityReport:
    schema_version: str
    generated_at: str
    measurement_scope: str
    hardware: HardwareProfile
    dataset: DatasetProfile
    plan: BenchmarkPlan
    workloads: dict[str, WorkloadReport]
    first_bottleneck: str
    limitations: tuple[str, ...]
    reproduction_command: str

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self)))

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class _Execution:
    latency_ms: float
    error_category: str | None


class _ConcurrencyTracker:
    def __init__(self) -> None:
        self._lock = Lock()
        self._active = 0
        self.maximum = 0

    def enter(self) -> None:
        with self._lock:
            self._active += 1
            self.maximum = max(self.maximum, self._active)

    def leave(self) -> None:
        with self._lock:
            self._active -= 1


def _percentile(values: Sequence[float], percentile: float) -> float:
    if not values:
        raise ValueError("cannot calculate a percentile without values")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * percentile
    lower = int(rank)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = rank - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def _execute(
    operation: Callable[[], None],
    tracker: _ConcurrencyTracker,
    timeout_seconds: float,
) -> _Execution:
    tracker.enter()
    started = perf_counter()
    category: str | None = None
    try:
        operation()
    except Exception as error:
        category = type(error).__name__
    finally:
        elapsed = perf_counter() - started
        tracker.leave()
    if category is None and elapsed > timeout_seconds:
        category = "timeout"
    return _Execution(elapsed * 1000, category)


def _measure(
    operation: Callable[[], None],
    *,
    concurrency: int,
    iterations: int,
    timeout_seconds: float,
) -> ScenarioMeasurement:
    tracker = _ConcurrencyTracker()
    started = perf_counter()
    with ThreadPoolExecutor(max_workers=concurrency) as executor:
        executions = tuple(
            executor.map(
                lambda _: _execute(operation, tracker, timeout_seconds),
                range(iterations),
            )
        )
    duration = perf_counter() - started
    categories = Counter(
        execution.error_category
        for execution in executions
        if execution.error_category is not None
    )
    errors = sum(categories.values())
    latencies = tuple(execution.latency_ms for execution in executions)
    return ScenarioMeasurement(
        concurrency=concurrency,
        requests=iterations,
        successes=iterations - errors,
        errors=errors,
        error_rate=round(errors / iterations, 6),
        duration_seconds=round(duration, 6),
        throughput_rps=round(iterations / duration, 3),
        latency_p50_ms=round(_percentile(latencies, 0.50), 3),
        latency_p95_ms=round(_percentile(latencies, 0.95), 3),
        latency_p99_ms=round(_percentile(latencies, 0.99), 3),
        max_in_flight=tracker.maximum,
        error_categories=dict(sorted(categories.items())),
    )


def find_saturation(
    measurements: Sequence[ScenarioMeasurement], *, gain_threshold: float
) -> int | None:
    """Return the first tested concurrency whose throughput gain is below threshold."""
    for previous, current in zip(measurements, measurements[1:], strict=False):
        if previous.throughput_rps <= 0:
            continue
        gain = (
            current.throughput_rps - previous.throughput_rps
        ) / previous.throughput_rps
        if gain < gain_threshold:
            return current.concurrency
    return None


def run_benchmark(
    *,
    operations: Mapping[str, Callable[[], None]],
    plan: BenchmarkPlan,
    hardware: HardwareProfile,
    dataset: DatasetProfile,
    generated_at: str,
    limitations: Sequence[str],
    reproduction_command: str,
) -> CapacityReport:
    if set(operations) != REQUIRED_WORKLOADS:
        raise ValueError("operations must contain search, ask and ingestion")
    workloads: dict[str, WorkloadReport] = {}
    for name in sorted(operations):
        operation = operations[name]
        warmup_tracker = _ConcurrencyTracker()
        for _ in range(plan.warmup_iterations):
            _execute(operation, warmup_tracker, plan.request_timeout_seconds)
        measurements = tuple(
            _measure(
                operation,
                concurrency=concurrency,
                iterations=plan.measured_iterations,
                timeout_seconds=plan.request_timeout_seconds,
            )
            for concurrency in plan.concurrency
        )
        workloads[name] = WorkloadReport(
            measurements,
            find_saturation(
                measurements,
                gain_threshold=plan.saturation_gain_threshold,
            ),
        )
    saturated = sorted(
        (workload.saturation_concurrency, name)
        for name, workload in workloads.items()
        if workload.saturation_concurrency is not None
    )
    first_bottleneck = (
        f"{saturated[0][1]} throughput plateau at concurrency {saturated[0][0]}"
        if saturated
        else "No throughput plateau observed within tested concurrency."
    )
    report = CapacityReport(
        schema_version=SCHEMA_VERSION,
        generated_at=generated_at,
        measurement_scope="local-component-capacity",
        hardware=hardware,
        dataset=dataset,
        plan=plan,
        workloads=workloads,
        first_bottleneck=first_bottleneck,
        limitations=tuple(limitations),
        reproduction_command=reproduction_command,
    )
    validate_report(report.to_dict())
    return report


def validate_report(report: Mapping[str, Any]) -> None:
    """Validate the stable capacity report contract without optional dependencies."""
    required = {
        "schema_version",
        "generated_at",
        "measurement_scope",
        "hardware",
        "dataset",
        "plan",
        "workloads",
        "first_bottleneck",
        "limitations",
        "reproduction_command",
    }
    if set(report) != required or report["schema_version"] != SCHEMA_VERSION:
        raise ValueError("invalid capacity report envelope")
    hardware = report["hardware"]
    if (
        not isinstance(hardware, Mapping)
        or int(hardware.get("logical_cpus", 0)) <= 0
        or float(hardware.get("memory_gb", 0)) <= 0
        or not str(hardware.get("cpu_model", "")).strip()
    ):
        raise ValueError("invalid hardware profile")
    dataset = report["dataset"]
    if (
        not isinstance(dataset, Mapping)
        or len(str(dataset.get("sha256", ""))) != 64
        or any(
            int(dataset.get(field, 0)) <= 0
            for field in ("documents", "chunks", "questions", "bytes")
        )
    ):
        raise ValueError("invalid dataset profile")
    plan = report["plan"]
    concurrency = (
        tuple(plan.get("concurrency", ())) if isinstance(plan, Mapping) else ()
    )
    if (
        not concurrency
        or any(int(value) <= 0 for value in concurrency)
        or int(plan.get("warmup_iterations", 0)) <= 0
        or int(plan.get("measured_iterations", 0)) <= 0
        or float(plan.get("request_timeout_seconds", 0)) <= 0
    ):
        raise ValueError("invalid benchmark plan")
    workloads = report["workloads"]
    if not isinstance(workloads, Mapping) or set(workloads) != REQUIRED_WORKLOADS:
        raise ValueError("invalid workloads")
    for workload in workloads.values():
        if not isinstance(workload, Mapping):
            raise ValueError("invalid workload")
        measurements = workload.get("measurements")
        if (
            not isinstance(measurements, Sequence)
            or isinstance(measurements, (str, bytes))
            or len(measurements) != len(concurrency)
        ):
            raise ValueError("invalid workload measurements")
        for expected_concurrency, measurement in zip(
            concurrency, measurements, strict=True
        ):
            _validate_measurement(measurement, int(expected_concurrency))
        saturation = workload.get("saturation_concurrency")
        if saturation is not None and saturation not in concurrency:
            raise ValueError("invalid saturation concurrency")
    if not str(report["first_bottleneck"]).strip():
        raise ValueError("first bottleneck is required")
    limitations = report["limitations"]
    if (
        not isinstance(limitations, Sequence)
        or isinstance(limitations, (str, bytes))
        or not limitations
    ):
        raise ValueError("limitations are required")
    if not str(report["reproduction_command"]).strip():
        raise ValueError("reproduction command is required")


def _validate_measurement(measurement: Any, expected_concurrency: int) -> None:
    if not isinstance(measurement, Mapping):
        raise ValueError("invalid measurement")
    requests = int(measurement.get("requests", 0))
    successes = int(measurement.get("successes", -1))
    errors = int(measurement.get("errors", -1))
    p50 = float(measurement.get("latency_p50_ms", -1))
    p95 = float(measurement.get("latency_p95_ms", -1))
    p99 = float(measurement.get("latency_p99_ms", -1))
    if (
        int(measurement.get("concurrency", 0)) != expected_concurrency
        or requests <= 0
        or successes + errors != requests
        or errors < 0
        or float(measurement.get("error_rate", -1)) != round(errors / requests, 6)
        or float(measurement.get("duration_seconds", 0)) <= 0
        or float(measurement.get("throughput_rps", 0)) <= 0
        or not 0 <= p50 <= p95 <= p99
        or not 1 <= int(measurement.get("max_in_flight", 0)) <= expected_concurrency
    ):
        raise ValueError("invalid measurement percentiles or counters")
    categories = measurement.get("error_categories")
    if not isinstance(categories, Mapping) or sum(categories.values()) != errors:
        raise ValueError("invalid error categories")


class _StaticSearchAdapter:
    def __init__(self, result: SearchResult) -> None:
        self._result = result

    def search(self, request: SearchRequest) -> SearchResult:
        if request.tenant_id != "portfolio":
            raise ValueError("unexpected benchmark tenant")
        return self._result


class _StaticRetriever:
    def __init__(self, result: EvidenceSet) -> None:
        self._result = result

    def retrieve(self, context: QueryContext) -> EvidenceSet:
        if context.principal.tenant_id != "portfolio":
            raise ValueError("unexpected benchmark tenant")
        return self._result


class _StaticProvider:
    def stream(self, request: GenerationRequest) -> Iterator[GenerationCompleted]:
        evidence = request.context.evidence[0]
        yield GenerationCompleted(
            GenerationResponse(
                (
                    ProposedClaim(
                        evidence.text,
                        (
                            ProposedCitation(
                                evidence.chunk_id,
                                evidence.document_version_id,
                                evidence.span,
                            ),
                        ),
                    ),
                ),
                "deterministic-local-provider",
                32,
                12,
            )
        )


def _local_fixture() -> tuple[Principal, Evidence, DocumentPolicy]:
    text = "Restart the worker only after draining its queue."
    principal = Principal("benchmark-user", "portfolio", ("operator",), ())
    evidence = Evidence(
        "chunk-1",
        "portfolio",
        "runbooks",
        "recovery-runbook",
        "recovery-runbook-v1",
        0,
        text,
        (0, len(text)),
        sha256(text.encode()).hexdigest(),
        MarkdownParser.VERSION,
        StructuralChunker.VERSION,
        datetime(2026, 9, 21, tzinfo=UTC),
        True,
        8.4,
        AuthorizationReason.ROLE,
    )
    policy = DocumentPolicy(
        evidence.tenant_id,
        evidence.source_id,
        evidence.document_id,
        evidence.document_version_id,
        ("role:operator",),
        False,
    )
    return principal, evidence, policy


def build_local_operations() -> tuple[dict[str, Callable[[], None]], DatasetProfile]:
    principal, evidence, policy = _local_fixture()
    hit = SearchHit(
        evidence.chunk_id,
        evidence.tenant_id,
        evidence.source_id,
        evidence.document_id,
        evidence.document_version_id,
        policy.policy,
        evidence.ordinal,
        evidence.text,
        evidence.span[0],
        evidence.span[1],
        evidence.content_hash,
        evidence.parser_version,
        evidence.chunker_version,
        evidence.source_timestamp,
        evidence.is_current,
        evidence.score,
    )
    store = SnapshotPolicyStore((policy,))
    search = BM25Retriever(
        _StaticSearchAdapter(SearchResult((hit,), 1, 1)),
        PolicyEnforcer(store),
    )
    evidence_set = EvidenceSet((evidence,), "bm25", 1, 1)
    ask = AskService(
        _StaticRetriever(evidence_set),
        ContextPacker(max_tokens=256),
        _StaticProvider(),
        CitationVerifier(PolicyEnforcer(store)),
        AbstentionDecider(),
        max_generation_attempts=1,
        retry_delays=(),
    )
    parser = MarkdownParser()
    chunker = StructuralChunker(max_tokens=40)

    def search_operation() -> None:
        result = search.retrieve(QueryContext("worker recovery", principal))
        if not result.evidence:
            raise RuntimeError("search returned no evidence")

    def ask_operation() -> None:
        session = ask.start(
            Question("capacity-question", "How should I restart?"), principal
        )
        events = tuple(session)
        if not events or events[-1].payload.get("status") != "verified":
            raise RuntimeError("ask did not produce a verified answer")

    def ingestion_operation() -> None:
        parsed = parser.parse(CORPUS)
        chunks = chunker.chunk(parsed)
        digests = tuple(sha256(chunk.text.encode()).digest() for chunk in chunks)
        if not digests:
            raise RuntimeError("ingestion produced no chunks")

    parsed = parser.parse(CORPUS)
    chunks = chunker.chunk(parsed)
    dataset = DatasetProfile(
        name="grounded-ops-local-capacity",
        version="1",
        sha256=sha256(CORPUS).hexdigest(),
        documents=1,
        chunks=len(chunks),
        questions=2,
        bytes=len(CORPUS),
    )
    return {
        "search": search_operation,
        "ask": ask_operation,
        "ingestion": ingestion_operation,
    }, dataset


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cpu-model", required=True)
    parser.add_argument("--memory-gb", type=float, required=True)
    parser.add_argument("--concurrency", nargs="+", type=int, default=[1, 2, 4, 8])
    parser.add_argument("--warmup", type=int, default=20)
    parser.add_argument("--iterations", type=int, default=200)
    parser.add_argument("--timeout-seconds", type=float, default=2.0)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    operations, dataset = build_local_operations()
    command = (
        "uv run python tests/operational/load/benchmark.py "
        f'--output {args.output.as_posix()} --cpu-model "{args.cpu_model}" '
        f"--memory-gb {args.memory_gb} --concurrency "
        f"{' '.join(str(value) for value in args.concurrency)} "
        f"--warmup {args.warmup} --iterations {args.iterations} "
        f"--timeout-seconds {args.timeout_seconds}"
    )
    report = run_benchmark(
        operations=operations,
        plan=BenchmarkPlan(
            concurrency=tuple(args.concurrency),
            warmup_iterations=args.warmup,
            measured_iterations=args.iterations,
            request_timeout_seconds=args.timeout_seconds,
            saturation_gain_threshold=0.10,
        ),
        hardware=HardwareProfile(
            operating_system=platform.platform(),
            architecture=platform.machine(),
            cpu_model=args.cpu_model,
            logical_cpus=os.cpu_count() or 1,
            memory_gb=args.memory_gb,
            python_version=platform.python_version(),
        ),
        dataset=dataset,
        generated_at=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        limitations=(
            "Local component benchmark; it does not validate production SLOs.",
            "OpenSearch, PostgreSQL, object storage and network latency are excluded.",
            "Ask uses a deterministic provider, so OpenAI latency and token "
            "throughput are excluded.",
            "Ingestion covers parsing, structural chunking and hashing, not "
            "persistence or indexing.",
            "Thread-based concurrency includes Python runtime and host scheduling "
            "effects.",
        ),
        reproduction_command=command,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.to_json(), encoding="utf-8")
    print(report.to_json(), end="")


if __name__ == "__main__":
    main()
