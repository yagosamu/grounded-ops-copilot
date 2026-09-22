"""Deterministic comparison of bounded Investigate against standard Ask."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import asdict, dataclass
from enum import StrEnum
from hashlib import sha256
from pathlib import Path

import yaml


class ExecutionOutcome(StrEnum):
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    TOOL_FAILURE = "tool_failure"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    PROVIDER_FAILURE = "provider_failure"
    BUDGET_EXHAUSTED = "budget_exhausted"


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    question: str
    expected_fact_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        if not self.id or not self.question.strip() or not self.expected_fact_ids:
            raise ValueError("benchmark case requires id, question and expected facts")
        if len(set(self.expected_fact_ids)) != len(self.expected_fact_ids):
            raise ValueError("benchmark expected facts must be unique")


@dataclass(frozen=True)
class ExecutionResult:
    case_id: str
    fact_ids: tuple[str, ...]
    outcome: ExecutionOutcome
    latency_ms: float
    cost_usd: float

    def __post_init__(self) -> None:
        if not self.case_id or self.latency_ms < 0 or self.cost_usd < 0:
            raise ValueError("invalid execution measurement")


@dataclass(frozen=True)
class EvaluatedCase:
    case_id: str
    success: bool
    failure_category: str | None
    latency_ms: float
    cost_usd: float


@dataclass(frozen=True)
class PathReport:
    path: str
    successes: int
    success_rate: float
    failure_rate: float
    mean_cost_usd: float
    latency_p95_ms: float
    failure_categories: dict[str, int]
    cases: tuple[EvaluatedCase, ...]


@dataclass(frozen=True)
class PromotionThresholds:
    minimum_relative_success_gain: float = 0.10
    maximum_cost_ratio: float = 2.5

    def __post_init__(self) -> None:
        if self.minimum_relative_success_gain < 0:
            raise ValueError("success threshold cannot be negative")
        if self.maximum_cost_ratio <= 0:
            raise ValueError("cost threshold must be positive")


DEFAULT_PROMOTION_THRESHOLDS = PromotionThresholds()


@dataclass(frozen=True)
class BenchmarkReport:
    dataset_hash: str
    judge_version: str
    measurement_mode: str
    case_count: int
    ask: PathReport
    investigate: PathReport
    success_delta: float
    relative_success_gain: float
    cost_ratio: float
    p95_ratio: float
    thresholds: PromotionThresholds
    latency_threshold_applied: bool
    qualified: bool
    rejections: tuple[str, ...]
    limitations: tuple[str, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


@dataclass(frozen=True)
class BenchmarkDataset:
    cases: tuple[BenchmarkCase, ...]
    ask_results: tuple[ExecutionResult, ...]
    investigate_results: tuple[ExecutionResult, ...]
    dataset_hash: str
    judge_version: str
    measurement_mode: str
    limitations: tuple[str, ...]

    def evaluation_inputs(self) -> dict[str, object]:
        return {
            "cases": self.cases,
            "ask_results": self.ask_results,
            "investigate_results": self.investigate_results,
            "dataset_hash": self.dataset_hash,
            "judge_version": self.judge_version,
            "measurement_mode": self.measurement_mode,
            "limitations": self.limitations,
        }


def evaluate(
    cases: tuple[BenchmarkCase, ...],
    ask_results: tuple[ExecutionResult, ...],
    investigate_results: tuple[ExecutionResult, ...],
    *,
    dataset_hash: str,
    judge_version: str,
    measurement_mode: str,
    limitations: tuple[str, ...],
    thresholds: PromotionThresholds = DEFAULT_PROMOTION_THRESHOLDS,
) -> BenchmarkReport:
    if not cases or not dataset_hash or not judge_version or not measurement_mode:
        raise ValueError("benchmark metadata and cases are required")
    case_ids = [case.id for case in cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("benchmark case ids must be unique")
    ask = _path_report("ask", cases, ask_results)
    investigate = _path_report("investigate", cases, investigate_results)
    if ask.mean_cost_usd <= 0 or ask.latency_p95_ms <= 0:
        raise ValueError("baseline cost and p95 must be positive")
    success_delta = investigate.success_rate - ask.success_rate
    relative_gain = _relative_delta(investigate.success_rate, ask.success_rate)
    cost_ratio = investigate.mean_cost_usd / ask.mean_cost_usd
    p95_ratio = investigate.latency_p95_ms / ask.latency_p95_ms
    rejections: list[str] = []
    if relative_gain < thresholds.minimum_relative_success_gain:
        rejections.append(
            "relative success gain below "
            f"{thresholds.minimum_relative_success_gain:.2%}"
        )
    if cost_ratio > thresholds.maximum_cost_ratio:
        rejections.append(f"cost ratio above {thresholds.maximum_cost_ratio:.2f}x")
    return BenchmarkReport(
        dataset_hash,
        judge_version,
        measurement_mode,
        len(cases),
        ask,
        investigate,
        success_delta,
        relative_gain,
        cost_ratio,
        p95_ratio,
        thresholds,
        False,
        not rejections,
        tuple(rejections),
        limitations,
    )


def load_dataset(path: Path) -> BenchmarkDataset:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    cases = tuple(
        BenchmarkCase(
            str(item["id"]),
            str(item["question"]),
            tuple(str(value) for value in item["expected_fact_ids"]),
        )
        for item in raw["cases"]
    )
    observations = raw["observations"]
    return BenchmarkDataset(
        cases,
        _load_results(observations["ask"]),
        _load_results(observations["investigate"]),
        sha256(path.read_bytes()).hexdigest(),
        str(raw["judge_version"]),
        str(raw["measurement_mode"]),
        tuple(str(value) for value in raw["limitations"]),
    )


def _load_results(items: list[dict[str, object]]) -> tuple[ExecutionResult, ...]:
    return tuple(
        ExecutionResult(
            str(item["case_id"]),
            tuple(str(value) for value in item["fact_ids"]),
            ExecutionOutcome(str(item["outcome"])),
            float(item["latency_ms"]),
            float(item["cost_usd"]),
        )
        for item in items
    )


def _path_report(
    path: str,
    cases: tuple[BenchmarkCase, ...],
    results: tuple[ExecutionResult, ...],
) -> PathReport:
    expected_ids = {case.id for case in cases}
    result_ids = [result.case_id for result in results]
    if len(result_ids) != len(set(result_ids)) or set(result_ids) != expected_ids:
        raise ValueError(f"{path} must measure every benchmark case exactly once")
    by_id = {result.case_id: result for result in results}
    evaluated = tuple(
        _evaluate_case(case, by_id[case.id])
        for case in sorted(cases, key=lambda x: x.id)
    )
    successes = sum(item.success for item in evaluated)
    failures: dict[str, int] = {}
    for item in evaluated:
        if item.failure_category is not None:
            failures[item.failure_category] = failures.get(item.failure_category, 0) + 1
    count = len(evaluated)
    return PathReport(
        path,
        successes,
        successes / count,
        (count - successes) / count,
        sum(item.cost_usd for item in evaluated) / count,
        _percentile([item.latency_ms for item in evaluated], 0.95),
        dict(sorted(failures.items())),
        evaluated,
    )


def _evaluate_case(case: BenchmarkCase, result: ExecutionResult) -> EvaluatedCase:
    expected = set(case.expected_fact_ids)
    success = result.outcome is ExecutionOutcome.COMPLETED and expected <= set(
        result.fact_ids
    )
    failure = None
    if not success:
        failure = (
            "incorrect"
            if result.outcome is ExecutionOutcome.COMPLETED
            else result.outcome.value
        )
    return EvaluatedCase(
        case.id,
        success,
        failure,
        result.latency_ms,
        result.cost_usd,
    )


def _relative_delta(candidate: float, baseline: float) -> float:
    if baseline == 0:
        return 0.0 if candidate == 0 else 1.0
    return (candidate - baseline) / baseline


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path(__file__).with_name("dataset-v1.yaml"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(__file__).parent / "reports" / "agentic-v1.json",
    )
    args = parser.parse_args()
    dataset = load_dataset(args.dataset)
    report = evaluate(**dataset.evaluation_inputs())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.to_json(), encoding="utf-8", newline="\n")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "qualified": report.qualified,
                "rejections": report.rejections,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
