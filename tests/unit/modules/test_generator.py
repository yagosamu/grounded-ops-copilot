"""Generation produces structured but never self-verified grounded answers."""

from datetime import UTC, datetime

import pytest

from domain.answering import Question, VerificationStatus
from modules.answering.context_packer import PackedContext
from modules.answering.generator import (
    AnswerGenerator,
    GenerationFailure,
    GenerationProviderError,
    GenerationRequest,
    GenerationResponse,
    ProposedCitation,
    ProposedClaim,
)
from modules.policy.authorizer import AuthorizationReason
from modules.retrieval.retriever import Evidence


def packed_context() -> PackedContext:
    item = Evidence(
        "chunk-1",
        "alpha",
        "source-1",
        "document-1",
        "version-2",
        0,
        "TracerProvider provides access to tracers.",
        (5, 45),
        "hash-1",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        4.2,
        AuthorizationReason.ROLE,
    )
    return PackedContext((item,), 5, 20, (), False)


def generated_response() -> GenerationResponse:
    return GenerationResponse(
        (
            ProposedClaim(
                "TracerProvider provides access to tracers.",
                (ProposedCitation("chunk-1", "version-2", (5, 45)),),
            ),
        ),
        "gpt-test",
        12,
        7,
    )


class FrozenProvider:
    def __init__(
        self, outcomes: list[GenerationResponse | GenerationProviderError]
    ) -> None:
        self.outcomes = outcomes
        self.requests: list[GenerationRequest] = []

    def generate(self, request: GenerationRequest) -> GenerationResponse:
        self.requests.append(request)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.mark.unit
def test_generates_a_structured_unverified_answer_with_usage() -> None:
    provider = FrozenProvider([generated_response()])
    generator = AnswerGenerator(provider, max_attempts=2, retry_delays=(0.0,))
    question = Question("question-1", "What provides access to tracers?")
    context = packed_context()

    answer = generator.generate(question, context)

    assert provider.requests == [GenerationRequest(question, context)]
    assert answer.question == question
    assert answer.claims[0].text == "TracerProvider provides access to tracers."
    assert answer.claims[0].citations[0].evidence_id == "chunk-1"
    assert answer.claims[0].citations[0].document_version_id == "version-2"
    assert answer.claims[0].citations[0].span == (5, 45)
    assert answer.claims[0].citations[0].resolved is False
    assert answer.status is VerificationStatus.UNVERIFIED
    assert answer.usage.model == "gpt-test"
    assert answer.usage.input_tokens == 12
    assert answer.usage.output_tokens == 7


@pytest.mark.unit
def test_retries_a_retryable_provider_failure_then_returns_the_same_contract() -> None:
    provider = FrozenProvider(
        [
            GenerationProviderError(GenerationFailure.UNAVAILABLE, retryable=True),
            generated_response(),
        ]
    )
    generator = AnswerGenerator(provider, max_attempts=2, retry_delays=(0.0,))

    answer = generator.generate(
        Question("question-1", "What provides access?"), packed_context()
    )

    assert answer.claims[0].citations[0].evidence_id == "chunk-1"
    assert len(provider.requests) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    ("failure", "attempts"),
    [
        (GenerationFailure.TIMEOUT, 2),
        (GenerationFailure.UNAVAILABLE, 2),
        (GenerationFailure.MALFORMED_OUTPUT, 1),
    ],
)
def test_reports_safe_bounded_provider_failures(
    failure: GenerationFailure, attempts: int
) -> None:
    provider = FrozenProvider(
        [GenerationProviderError(failure, retryable=attempts == 2)] * attempts
    )
    generator = AnswerGenerator(provider, max_attempts=2, retry_delays=(0.0,))

    with pytest.raises(GenerationProviderError) as caught:
        generator.generate(Question("question-1", "Will this fail?"), packed_context())

    assert caught.value.failure is failure
    assert str(caught.value) == f"generation {failure.value}"
    assert len(provider.requests) == attempts


@pytest.mark.unit
def test_rejects_a_fabricated_or_malformed_provider_citation() -> None:
    response = GenerationResponse(
        (
            ProposedClaim(
                "Fabricated",
                (ProposedCitation("missing", "version-9", (0, 4)),),
            ),
        ),
        "gpt-test",
        1,
        1,
    )
    generator = AnswerGenerator(FrozenProvider([response]))

    with pytest.raises(GenerationProviderError) as caught:
        generator.generate(Question("question-1", "Fabricate?"), packed_context())

    assert caught.value.failure is GenerationFailure.MALFORMED_OUTPUT
    assert caught.value.retryable is False
