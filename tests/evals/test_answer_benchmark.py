"""The grounded-answer benchmark is deterministic and enforces promotion policy."""

from dataclasses import replace
from pathlib import Path

import pytest

from evals.answering.benchmark import (
    AnswerCase,
    ModelConfig,
    load_dataset,
    run_benchmark,
)
from modules.answering.generator import (
    GenerationRequest,
    GenerationResponse,
    ProposedCitation,
    ProposedClaim,
)


class FrozenProvider:
    def __init__(self, model: str, *, fail_difficult: bool = False) -> None:
        self.model = model
        self.fail_difficult = fail_difficult

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        evidence = request.context.evidence
        if self.fail_difficult and len(evidence) > 1:
            claims = (
                ProposedClaim(
                    (
                        "A Resource represents the entity producing telemetry "
                        "as resource attributes."
                    ),
                    (
                        ProposedCitation(
                            evidence[0].chunk_id,
                            evidence[0].document_version_id,
                            evidence[0].span,
                        ),
                    ),
                ),
            )
        else:
            claims = tuple(
                ProposedClaim(
                    item.text.splitlines()[-1],
                    (
                        ProposedCitation(
                            item.chunk_id,
                            item.document_version_id,
                            item.span,
                        ),
                    ),
                )
                for item in evidence
            )
        return GenerationResponse(claims, self.model, 100, 25)


class FrozenClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        self.value += 0.01
        return self.value


@pytest.fixture
def dataset() -> tuple[tuple[AnswerCase, ...], str, str]:
    root = Path(__file__).parents[2]
    return load_dataset(root / "evals/answering/dataset-v1.yaml")


@pytest.mark.answer_eval
def test_measures_grounding_abstention_quality_latency_and_cost(
    dataset: tuple[tuple[AnswerCase, ...], str, str],
) -> None:
    cases, dataset_hash, judge_version = dataset
    configs = (
        ModelConfig("gpt-5-nano", 0.05, 0.40),
        ModelConfig("gpt-4o-mini", 0.15, 0.60),
        ModelConfig("gpt-5.6-luna", 0.20, 1.20),
        ModelConfig("gpt-5.6-terra", 2.00, 12.00, difficult_only=True),
    )

    report = run_benchmark(
        cases,
        lambda model: FrozenProvider(model),
        dataset_hash=dataset_hash,
        judge_version=judge_version,
        model_configs=configs,
        clock=FrozenClock(),
    )

    nano = report.models[0]
    terra = report.models[3]
    assert nano.case_count == 4
    assert nano.citation_coverage == 1.0
    assert nano.invalid_citations == 0
    assert nano.input_usd_per_million == 0.05
    assert nano.output_usd_per_million == 0.40
    assert nano.abstention_accuracy == 1.0
    assert nano.task_success == 1.0
    assert nano.latency_p50_ms == pytest.approx(10.0)
    assert nano.latency_p95_ms == pytest.approx(10.0)
    assert nano.total_cost_usd == pytest.approx(0.000045)
    assert terra.case_count == 1
    assert terra.rejection == "upper bound only; cost reduction below 30%"
    assert report.selected_model == "gpt-5-nano"
    assert report.baseline_model == "gpt-5.6-luna"
    assert report.judge_version == "expected-facts-v1"
    assert report.pricing_snapshot == "2026-09-18"
    assert report.measurement_mode.startswith("live OpenAI Responses API")
    assert len(report.limitations) == 3
    tracing = nano.cases[0]
    assert tracing.citations[0].evidence_id == "otel-tracing-api-chunk-0"
    assert tracing.citations[0].document_version_id == "otel-tracing-api-v1"
    assert tracing.citations[0].valid is True


@pytest.mark.answer_eval
def test_rejects_quality_regression_and_report_is_repeatable(
    dataset: tuple[tuple[AnswerCase, ...], str, str],
) -> None:
    cases, dataset_hash, judge_version = dataset
    configs = (
        ModelConfig("gpt-5-nano", 0.05, 0.40),
        ModelConfig("gpt-4o-mini", 0.15, 0.60),
        ModelConfig("gpt-5.6-luna", 0.20, 1.20),
    )

    def factory(model: str) -> FrozenProvider:
        return FrozenProvider(model, fail_difficult=model == "gpt-5-nano")

    first = run_benchmark(
        cases,
        factory,
        dataset_hash=dataset_hash,
        judge_version=judge_version,
        model_configs=configs,
        clock=FrozenClock(),
    )
    second = run_benchmark(
        cases,
        factory,
        dataset_hash=dataset_hash,
        judge_version=judge_version,
        model_configs=configs,
        clock=FrozenClock(),
    )

    assert first == second
    assert first.to_json() == second.to_json()
    assert first.models[0].qualified is False
    assert first.models[0].rejection == "task success regressed by more than 2pp"
    assert first.selected_model == "gpt-4o-mini"


@pytest.mark.answer_eval
def test_loads_versioned_dataset_and_difficult_subset(
    dataset: tuple[tuple[AnswerCase, ...], str, str],
) -> None:
    cases, dataset_hash, judge_version = dataset
    assert len(cases) == 4
    assert dataset_hash
    assert judge_version == "expected-facts-v1"
    assert cases[-1].difficult is True
    assert len(cases[-1].evidence) == 2


@pytest.mark.answer_eval
def test_terra_scope_does_not_hide_a_missing_difficult_case(
    dataset: tuple[tuple[AnswerCase, ...], str, str],
) -> None:
    cases, dataset_hash, judge_version = dataset
    easy_only = tuple(replace(case, difficult=False) for case in cases)

    with pytest.raises(ValueError, match="has no benchmark cases"):
        run_benchmark(
            easy_only,
            lambda model: FrozenProvider(model),
            dataset_hash=dataset_hash,
            judge_version=judge_version,
            clock=FrozenClock(),
        )
