"""Reproducible grounded-answer benchmark and model promotion policy."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Protocol

import yaml

from adapters.policy.snapshot import SnapshotPolicyStore
from domain.answering import Question, VerificationStatus
from modules.answering.abstention import AbstentionDecider
from modules.answering.context_packer import ContextPacker
from modules.answering.generator import (
    AnswerGenerator,
    GenerationProvider,
    GenerationProviderError,
    OpenAIGenerationProvider,
)
from modules.answering.verifier import CitationVerifier
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.retriever import Evidence, EvidenceSet


@dataclass(frozen=True)
class ModelConfig:
    model: str
    input_usd_per_million: float
    output_usd_per_million: float
    difficult_only: bool = False


MODEL_CONFIGS = (
    ModelConfig("gpt-5-nano", 0.05, 0.40),
    ModelConfig("gpt-4o-mini", 0.15, 0.60),
    ModelConfig("gpt-5.6-luna", 0.20, 1.20),
    ModelConfig("gpt-5.6-terra", 2.00, 12.00, difficult_only=True),
)


@dataclass(frozen=True)
class AnswerCase:
    id: str
    question: str
    evidence: tuple[Evidence, ...]
    expected_facts: tuple[str, ...]
    should_abstain: bool
    difficult: bool


@dataclass(frozen=True)
class CitationResult:
    evidence_id: str
    document_version_id: str
    span: tuple[int, int]
    valid: bool


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    evidence_ids: tuple[str, ...]
    status: str
    claims: tuple[str, ...]
    citations: tuple[CitationResult, ...]
    citation_count: int
    valid_citation_count: int
    expected_fact_matches: tuple[bool, ...]
    task_success: bool
    abstention_correct: bool | None
    latency_ms: float
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None


@dataclass(frozen=True)
class ModelReport:
    model: str
    config_hash: str
    input_usd_per_million: float
    output_usd_per_million: float
    case_count: int
    citation_coverage: float
    invalid_citations: int
    abstention_accuracy: float
    task_success: float
    latency_p50_ms: float
    latency_p95_ms: float
    total_cost_usd: float
    qualified: bool
    rejection: str | None
    cases: tuple[CaseResult, ...]


@dataclass(frozen=True)
class BenchmarkReport:
    dataset_hash: str
    judge_version: str
    pricing_snapshot: str
    measurement_mode: str
    baseline_model: str
    selected_model: str | None
    limitations: tuple[str, ...]
    models: tuple[ModelReport, ...]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True) + "\n"


class ProviderFactory(Protocol):
    def __call__(self, model: str) -> GenerationProvider: ...


def load_dataset(path: Path) -> tuple[tuple[AnswerCase, ...], str, str]:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    source_evidence: dict[str, Evidence] = {}
    digest = sha256()
    digest.update(path.read_bytes())
    for source in raw["sources"]:
        fixture = (path.parent / source["fixture"]).resolve()
        text = fixture.read_text(encoding="utf-8").strip()
        digest.update(fixture.relative_to(path.parents[1]).as_posix().encode())
        digest.update(b"\0")
        digest.update(text.encode())
        source_id = str(source["id"])
        source_evidence[source_id] = Evidence(
            f"{source_id}-chunk-0",
            "public-eval",
            source_id,
            str(source["document_id"]),
            str(source["document_version_id"]),
            0,
            text,
            (0, len(text)),
            sha256(text.encode()).hexdigest(),
            "markdown-v1",
            "structural-v1",
            datetime(2026, 9, 18, tzinfo=UTC),
            True,
            1.0,
            AuthorizationReason.PUBLIC,
        )
    cases = tuple(
        AnswerCase(
            str(item["id"]),
            str(item["question"]),
            tuple(source_evidence[value] for value in item["evidence_source_ids"]),
            tuple(str(value) for value in item["expected_facts"]),
            bool(item["should_abstain"]),
            bool(item["difficult"]),
        )
        for item in raw["cases"]
    )
    return cases, digest.hexdigest(), str(raw["judge_version"])


def run_benchmark(
    cases: tuple[AnswerCase, ...],
    provider_factory: ProviderFactory,
    *,
    dataset_hash: str,
    judge_version: str,
    model_configs: tuple[ModelConfig, ...] = MODEL_CONFIGS,
    clock: Callable[[], float] = perf_counter,
) -> BenchmarkReport:
    if not cases or not judge_version.strip():
        raise ValueError("benchmark requires cases and a pinned judge")
    raw_reports = tuple(
        _run_model(
            cases,
            config,
            provider_factory(config.model),
            judge_version,
            clock,
        )
        for config in model_configs
    )
    reports, selected = _apply_promotion(raw_reports)
    return BenchmarkReport(
        dataset_hash,
        judge_version,
        "2026-09-18",
        "live OpenAI Responses API; sequential local execution",
        "gpt-5.6-luna",
        selected,
        (
            "dataset v1 contains four cases and two short public source excerpts",
            "latency is a local sequential measurement, not a production load test",
            "expected-facts-v1 is a deterministic lexical judge",
        ),
        reports,
    )


def _run_model(
    cases: tuple[AnswerCase, ...],
    config: ModelConfig,
    provider: GenerationProvider,
    judge_version: str,
    clock: Callable[[], float],
) -> ModelReport:
    selected_cases = tuple(
        case for case in cases if not config.difficult_only or case.difficult
    )
    if not selected_cases:
        raise ValueError(f"model {config.model} has no benchmark cases")
    results = tuple(_run_case(case, config, provider, clock) for case in selected_cases)
    claim_count = sum(len(item.claims) for item in results)
    cited_claim_count = sum(
        len(item.claims) if item.citation_count > 0 else 0 for item in results
    )
    abstentions = tuple(
        item.abstention_correct
        for item in results
        if item.abstention_correct is not None
    )
    config_payload = json.dumps(
        {"judge_version": judge_version, **asdict(config)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return ModelReport(
        config.model,
        sha256(config_payload).hexdigest(),
        config.input_usd_per_million,
        config.output_usd_per_million,
        len(results),
        cited_claim_count / claim_count if claim_count else 1.0,
        sum(item.citation_count - item.valid_citation_count for item in results),
        sum(abstentions) / len(abstentions) if abstentions else 1.0,
        sum(item.task_success for item in results) / len(results),
        _percentile([item.latency_ms for item in results], 0.50),
        _percentile([item.latency_ms for item in results], 0.95),
        sum(item.cost_usd for item in results),
        False,
        "promotion not evaluated",
        results,
    )


def _run_case(
    case: AnswerCase,
    config: ModelConfig,
    provider: GenerationProvider,
    clock: Callable[[], float],
) -> CaseResult:
    evidence_set = EvidenceSet(case.evidence, "answer-eval", len(case.evidence), 0)
    context = ContextPacker(max_tokens=2_000).pack(evidence_set)
    abstention = AbstentionDecider().decide(context)
    if abstention is not None:
        return CaseResult(
            case.id,
            tuple(item.chunk_id for item in case.evidence),
            VerificationStatus.ABSTAINED.value,
            (),
            (),
            0,
            0,
            tuple(False for _ in case.expected_facts),
            case.should_abstain,
            case.should_abstain,
            0.0,
            0,
            0,
            0.0,
            None,
        )
    started = clock()
    try:
        draft = AnswerGenerator(provider).generate(
            _question(case),
            context,
        )
        policies = tuple(
            DocumentPolicy(
                item.tenant_id,
                item.source_id,
                item.document_id,
                item.document_version_id,
                ("public",),
                False,
            )
            for item in case.evidence
        )
        principal = Principal("answer-eval", case.evidence[0].tenant_id, (), ())
        verification = CitationVerifier(
            PolicyEnforcer(SnapshotPolicyStore(policies))
        ).verify(draft, evidence_set, principal)
    except GenerationProviderError as error:
        latency = (clock() - started) * 1_000
        return CaseResult(
            case.id,
            tuple(item.chunk_id for item in case.evidence),
            "provider_failure",
            (),
            (),
            0,
            0,
            tuple(False for _ in case.expected_facts),
            False,
            None,
            latency,
            0,
            0,
            0.0,
            error.failure.value,
        )
    latency = (clock() - started) * 1_000
    answer = verification.answer
    claims = tuple(claim.text for claim in answer.claims)
    evidence_identities = {
        (item.chunk_id, item.document_version_id, item.span)
        for item in evidence_set.evidence
    }
    citations = tuple(
        CitationResult(
            citation.evidence_id,
            citation.document_version_id,
            citation.span,
            (
                citation.evidence_id,
                citation.document_version_id,
                citation.span,
            )
            in evidence_identities,
        )
        for claim in answer.claims
        for citation in claim.citations
    )
    citation_count = len(citations)
    valid_citations = sum(citation.valid for citation in citations)
    matches = tuple(_fact_is_present(fact, claims) for fact in case.expected_facts)
    success = (
        not case.should_abstain
        and answer.status is VerificationStatus.VERIFIED
        and all(matches)
    )
    cost = (
        answer.usage.input_tokens * config.input_usd_per_million
        + answer.usage.output_tokens * config.output_usd_per_million
    ) / 1_000_000
    return CaseResult(
        case.id,
        tuple(item.chunk_id for item in case.evidence),
        answer.status.value,
        claims,
        citations,
        citation_count,
        valid_citations,
        matches,
        success,
        not success if case.should_abstain else None,
        latency,
        answer.usage.input_tokens,
        answer.usage.output_tokens,
        cost,
        None if not verification.failures else verification.failures[0].value,
    )


def _question(case: AnswerCase) -> Question:
    return Question(case.id, case.question)


def _apply_promotion(
    reports: tuple[ModelReport, ...],
) -> tuple[tuple[ModelReport, ...], str | None]:
    baseline = next(item for item in reports if item.model == "gpt-5.6-luna")
    evaluated: list[ModelReport] = []
    for report in reports:
        reasons: list[str] = []
        if report.model == "gpt-5.6-terra":
            reasons.append("upper bound only")
        if report.citation_coverage < 0.95:
            reasons.append("citation coverage below 95%")
        if report.invalid_citations:
            reasons.append("invalid citations present")
        if report.task_success < baseline.task_success - 0.02:
            reasons.append("task success regressed by more than 2pp")
        if report.total_cost_usd > baseline.total_cost_usd * 0.70:
            reasons.append("cost reduction below 30%")
        qualified = not reasons
        evaluated.append(
            replace(
                report,
                qualified=qualified,
                rejection="; ".join(reasons) if reasons else None,
            )
        )
    candidates = [item for item in evaluated if item.qualified]
    selected = (
        min(candidates, key=lambda item: item.total_cost_usd).model
        if candidates
        else None
    )
    return tuple(evaluated), selected


def _fact_is_present(fact: str, claims: tuple[str, ...]) -> bool:
    fact_tokens = _tokens(fact)
    claim_tokens = (
        set().union(*(_tokens(claim) for claim in claims)) if claims else set()
    )
    return fact_tokens <= claim_tokens


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))


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
        default=Path(__file__).parent / "reports" / "answering-v1.json",
    )
    args = parser.parse_args()
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        parser.error("OPENAI_API_KEY is required")
    cases, dataset_hash, judge_version = load_dataset(args.dataset)
    report = run_benchmark(
        cases,
        lambda model: OpenAIGenerationProvider(
            api_key=api_key,
            model=model,
            timeout_seconds=60.0,
            max_output_tokens=1_200,
        ),
        dataset_hash=dataset_hash,
        judge_version=judge_version,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(report.to_json(), encoding="utf-8")
    print(
        json.dumps(
            {
                "output": str(args.output),
                "selected_model": report.selected_model,
            },
            sort_keys=True,
        )
    )
    return 0 if report.selected_model else 1


if __name__ == "__main__":
    raise SystemExit(main())
