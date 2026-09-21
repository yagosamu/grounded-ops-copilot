from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).with_name("benchmark.py")
REPORT_PATH = Path(__file__).with_name("reports") / "capacity-local-v1.json"


def _load_benchmark_module() -> Any:
    spec = importlib.util.spec_from_file_location("capacity_benchmark", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the capacity benchmark module")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


benchmark = _load_benchmark_module()
pytestmark = pytest.mark.operational


def _plan(*, concurrency: tuple[int, ...] = (1, 2), iterations: int = 8) -> Any:
    return benchmark.BenchmarkPlan(
        concurrency=concurrency,
        warmup_iterations=1,
        measured_iterations=iterations,
        request_timeout_seconds=1.0,
        saturation_gain_threshold=0.10,
    )


def _hardware() -> Any:
    return benchmark.HardwareProfile(
        operating_system="test-os",
        architecture="x86_64",
        cpu_model="test-cpu",
        logical_cpus=4,
        memory_gb=8.0,
        python_version="3.13.0",
    )


def _dataset() -> Any:
    return benchmark.DatasetProfile(
        name="self-check",
        version="1",
        sha256="a" * 64,
        documents=2,
        chunks=4,
        questions=3,
        bytes=1024,
    )


def _run(operations: dict[str, Any], *, plan: Any | None = None) -> Any:
    return benchmark.run_benchmark(
        operations=operations,
        plan=plan or _plan(),
        hardware=_hardware(),
        dataset=_dataset(),
        generated_at="2026-09-21T12:00:00Z",
        limitations=["Self-check uses deterministic in-process operations."],
        reproduction_command=(
            "pytest tests/operational/load/test_capacity_benchmark.py"
        ),
    )


def test_report_captures_required_capacity_dimensions() -> None:
    report = _run(
        {
            "search": lambda: None,
            "ask": lambda: None,
            "ingestion": lambda: None,
        }
    )
    payload = report.to_dict()

    benchmark.validate_report(payload)
    assert payload["schema_version"] == "capacity-report.v1"
    assert set(payload["workloads"]) == {"search", "ask", "ingestion"}
    assert payload["hardware"]["cpu_model"] == "test-cpu"
    assert payload["dataset"]["sha256"] == "a" * 64
    assert payload["plan"]["concurrency"] == [1, 2]
    assert payload["plan"]["warmup_iterations"] == 1
    assert payload["first_bottleneck"]

    for workload in payload["workloads"].values():
        for measurement in workload["measurements"]:
            assert measurement["requests"] == 8
            assert measurement["successes"] + measurement["errors"] == 8
            assert measurement["throughput_rps"] > 0
            assert measurement["latency_p50_ms"] <= measurement["latency_p95_ms"]
            assert measurement["latency_p95_ms"] <= measurement["latency_p99_ms"]


def test_error_behavior_is_counted_without_leaking_error_messages() -> None:
    def failing_operation() -> None:
        raise RuntimeError("secret-value-must-not-appear")

    report = _run(
        {
            "search": lambda: None,
            "ask": failing_operation,
            "ingestion": lambda: None,
        },
        plan=_plan(concurrency=(1,), iterations=4),
    )
    payload = report.to_dict()
    ask = payload["workloads"]["ask"]["measurements"][0]

    assert ask["errors"] == 4
    assert ask["error_rate"] == 1.0
    assert ask["error_categories"] == {"RuntimeError": 4}
    assert "secret-value-must-not-appear" not in json.dumps(payload)


def test_first_plateau_marks_saturation() -> None:
    measurements = [
        benchmark.ScenarioMeasurement.sample(concurrency=1, throughput_rps=10.0),
        benchmark.ScenarioMeasurement.sample(concurrency=2, throughput_rps=18.0),
        benchmark.ScenarioMeasurement.sample(concurrency=4, throughput_rps=19.0),
    ]

    assert benchmark.find_saturation(measurements, gain_threshold=0.10) == 4


def test_schema_rejects_missing_workload_and_invalid_percentiles() -> None:
    payload = _run(
        {
            "search": lambda: None,
            "ask": lambda: None,
            "ingestion": lambda: None,
        },
        plan=_plan(concurrency=(1,), iterations=2),
    ).to_dict()
    payload["workloads"].pop("ask")

    with pytest.raises(ValueError, match="workloads"):
        benchmark.validate_report(payload)

    payload = _run(
        {
            "search": lambda: None,
            "ask": lambda: None,
            "ingestion": lambda: None,
        },
        plan=_plan(concurrency=(1,), iterations=2),
    ).to_dict()
    payload["workloads"]["search"]["measurements"][0]["latency_p95_ms"] = -1

    with pytest.raises(ValueError, match="percentiles"):
        benchmark.validate_report(payload)


def test_committed_capacity_report_matches_the_schema() -> None:
    payload = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    benchmark.validate_report(payload)
    assert payload["measurement_scope"] == "local-component-capacity"
    assert payload["first_bottleneck"]
    assert len(payload["limitations"]) >= 3
    assert payload["reproduction_command"]
