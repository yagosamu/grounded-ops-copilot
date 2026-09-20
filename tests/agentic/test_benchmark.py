"""AGT-02 agentic value benchmark and promotion-policy tests."""

from dataclasses import replace
from pathlib import Path

import pytest

from evals.agentic.benchmark import (
    BenchmarkCase,
    ExecutionOutcome,
    ExecutionResult,
    PromotionThresholds,
    evaluate,
    load_dataset,
)

pytestmark = pytest.mark.agentic
ROOT = Path(__file__).parents[2]


def result(
    case_id: str,
    *,
    facts: tuple[str, ...] = ("fact-a", "fact-b"),
    outcome: ExecutionOutcome = ExecutionOutcome.COMPLETED,
    latency_ms: float = 100.0,
    cost_usd: float = 1.0,
) -> ExecutionResult:
    return ExecutionResult(case_id, facts, outcome, latency_ms, cost_usd)


def test_calculates_quality_cost_latency_and_failure_categories() -> None:
    cases = tuple(
        BenchmarkCase(f"case-{index}", "Compare sources", ("fact-a", "fact-b"))
        for index in range(1, 5)
    )
    ask = (
        result("case-1", latency_ms=10),
        result("case-2", latency_ms=20),
        result("case-3", facts=("fact-a",), latency_ms=30),
        result("case-4", facts=(), latency_ms=40),
    )
    investigate = (
        result("case-1", latency_ms=100, cost_usd=2),
        result("case-2", latency_ms=200, cost_usd=2),
        result("case-3", latency_ms=300, cost_usd=2),
        result(
            "case-4",
            facts=(),
            outcome=ExecutionOutcome.TIMEOUT,
            latency_ms=400,
            cost_usd=2,
        ),
    )

    report = evaluate(
        cases,
        ask,
        investigate,
        dataset_hash="a" * 64,
        judge_version="fact-id-v1",
        measurement_mode="deterministic fixture",
        limitations=("not a load test",),
    )

    assert report.ask.success_rate == 0.5
    assert report.investigate.success_rate == 0.75
    assert report.success_delta == 0.25
    assert report.relative_success_gain == 0.5
    assert report.cost_ratio == 2.0
    assert report.p95_ratio == pytest.approx(10.0)
    assert report.ask.failure_categories == {"incorrect": 2}
    assert report.investigate.failure_categories == {"timeout": 1}
    assert report.qualified is True
    assert report.rejections == ()
    assert report.latency_threshold_applied is False


@pytest.mark.parametrize(
    ("candidate", "expected"),
    [
        (
            (
                result("case-1", cost_usd=2),
                result("case-2", facts=(), cost_usd=2),
            ),
            ("relative success gain below 10.00%",),
        ),
        (
            (
                result("case-1", cost_usd=3),
                result("case-2", cost_usd=3),
            ),
            ("cost ratio above 2.50x",),
        ),
    ],
)
def test_rejects_each_failed_promotion_threshold(candidate, expected) -> None:
    cases = (
        BenchmarkCase("case-1", "Compare A", ("fact-a", "fact-b")),
        BenchmarkCase("case-2", "Compare B", ("fact-a", "fact-b")),
    )
    baseline = (
        result("case-1"),
        result("case-2", facts=()),
    )

    report = evaluate(
        cases,
        baseline,
        candidate,
        dataset_hash="b" * 64,
        judge_version="fact-id-v1",
        measurement_mode="deterministic fixture",
        limitations=(),
        thresholds=PromotionThresholds(0.10, 2.5),
    )

    assert report.qualified is False
    assert report.rejections == expected


def test_frozen_report_is_repeatable_and_covers_required_failure_modes() -> None:
    dataset = load_dataset(ROOT / "evals/agentic/dataset-v1.yaml")

    first = evaluate(**dataset.evaluation_inputs())
    second = evaluate(**dataset.evaluation_inputs())
    committed = (ROOT / "evals/agentic/reports/agentic-v1.json").read_text(
        encoding="utf-8"
    )

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.to_json() == committed
    assert first.case_count == 8
    assert first.investigate.failure_categories == {
        "insufficient_evidence": 1,
        "timeout": 1,
        "tool_failure": 1,
    }
    assert first.relative_success_gain == 0.25
    assert first.cost_ratio == pytest.approx(2.7)
    assert first.qualified is False
    assert first.rejections == ("cost ratio above 2.50x",)


def test_rejects_missing_duplicate_or_non_positive_baseline_measurements() -> None:
    cases = (
        BenchmarkCase("case-1", "Compare A", ("fact-a",)),
        BenchmarkCase("case-2", "Compare B", ("fact-b",)),
    )
    valid = (result("case-1"), result("case-2", facts=("fact-b",)))

    with pytest.raises(ValueError, match="exactly once"):
        evaluate(
            cases,
            valid,
            (result("case-1"), result("case-1")),
            dataset_hash="c" * 64,
            judge_version="fact-id-v1",
            measurement_mode="fixture",
            limitations=(),
        )

    zero_cost = tuple(replace(item, cost_usd=0.0) for item in valid)
    with pytest.raises(ValueError, match="baseline cost and p95"):
        evaluate(
            cases,
            zero_cost,
            valid,
            dataset_hash="c" * 64,
            judge_version="fact-id-v1",
            measurement_mode="fixture",
            limitations=(),
        )
