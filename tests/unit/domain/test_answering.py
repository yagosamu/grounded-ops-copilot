"""Grounded-answer contracts make verification state impossible to forge."""

from dataclasses import FrozenInstanceError

import pytest

from domain.answering import (
    AbstentionReason,
    AnswerUsage,
    Citation,
    Claim,
    GroundedAnswer,
    Question,
    VerificationStatus,
)


def citation(*, resolved: bool = True) -> Citation:
    return Citation("chunk-1", "version-2", (5, 35), resolved=resolved)


@pytest.mark.unit
def test_creates_an_immutable_verified_answer_from_resolved_claims() -> None:
    question = Question("question-1", "What provides access to tracers?")
    answer = GroundedAnswer(
        question,
        (Claim("TracerProvider provides access to tracers.", (citation(),)),),
        VerificationStatus.VERIFIED,
        AnswerUsage("gpt-test", 12, 7),
    )

    assert answer.question == question
    assert answer.claims[0].text == "TracerProvider provides access to tracers."
    assert answer.claims[0].citations == (citation(),)
    assert answer.status is VerificationStatus.VERIFIED
    assert answer.usage == AnswerUsage("gpt-test", 12, 7)
    assert answer.abstention_reason is None
    with pytest.raises(FrozenInstanceError):
        answer.status = VerificationStatus.UNVERIFIED


@pytest.mark.unit
@pytest.mark.parametrize(
    ("claims", "message"),
    [
        ((Claim("Unsupported fact", ()),), "verified claim requires a citation"),
        (
            (Claim("Unresolved fact", (citation(resolved=False),)),),
            "verified answer requires resolved citations",
        ),
        ((), "verified answer requires claims"),
    ],
)
def test_rejects_verified_answers_without_resolvable_claim_citations(
    claims: tuple[Claim, ...], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        GroundedAnswer(
            Question("question-1", "What provides access?"),
            claims,
            VerificationStatus.VERIFIED,
            AnswerUsage("gpt-test", 1, 1),
        )


@pytest.mark.unit
@pytest.mark.parametrize("span", [(-1, 2), (3, 3), (4, 3)])
def test_rejects_invalid_citation_spans(span: tuple[int, int]) -> None:
    with pytest.raises(ValueError, match="invalid citation span"):
        Citation("chunk-1", "version-2", span, resolved=True)


@pytest.mark.unit
def test_abstention_requires_a_reason_and_contains_no_claims() -> None:
    question = Question("question-1", "What is missing?")
    usage = AnswerUsage("gpt-test", 2, 0)

    answer = GroundedAnswer(
        question,
        (),
        VerificationStatus.ABSTAINED,
        usage,
        AbstentionReason.INSUFFICIENT_EVIDENCE,
    )

    assert answer.status is VerificationStatus.ABSTAINED
    assert answer.claims == ()
    assert answer.abstention_reason is AbstentionReason.INSUFFICIENT_EVIDENCE
    with pytest.raises(ValueError, match="abstained answer requires a reason"):
        GroundedAnswer(question, (), VerificationStatus.ABSTAINED, usage)
    with pytest.raises(ValueError, match="abstained answer cannot contain claims"):
        GroundedAnswer(
            question,
            (Claim("A fact", (citation(),)),),
            VerificationStatus.ABSTAINED,
            usage,
            AbstentionReason.INSUFFICIENT_EVIDENCE,
        )


@pytest.mark.unit
def test_non_abstained_answer_rejects_an_abstention_reason() -> None:
    with pytest.raises(ValueError, match="reason requires abstained status"):
        GroundedAnswer(
            Question("question-1", "What provides access?"),
            (Claim("A draft", (citation(resolved=False),)),),
            VerificationStatus.UNVERIFIED,
            AnswerUsage("gpt-test", 1, 1),
            AbstentionReason.INSUFFICIENT_EVIDENCE,
        )
