"""Abstention is a stable evidence decision, not a provider failure."""

from datetime import UTC, datetime

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
from modules.answering.abstention import AbstentionDecider
from modules.answering.context_packer import PackedContext
from modules.answering.verifier import VerificationFailure, VerificationResult
from modules.policy.authorizer import AuthorizationReason
from modules.retrieval.retriever import Evidence


def packed(*, conflicts: tuple[tuple[str, tuple[str, ...]], ...] = ()) -> PackedContext:
    current = Evidence(
        "chunk-current",
        "alpha",
        "source-1",
        "document-1",
        "version-2",
        0,
        "Use the new endpoint.",
        (0, 21),
        "hash-current",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        4.0,
        AuthorizationReason.PUBLIC,
    )
    old = Evidence(
        "chunk-old",
        "alpha",
        "source-1",
        "document-1",
        "version-1",
        0,
        "Use the old endpoint.",
        (0, 21),
        "hash-old",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 8, 17, tzinfo=UTC),
        False,
        3.0,
        AuthorizationReason.PUBLIC,
    )
    evidence = (current, old) if conflicts else (current,)
    return PackedContext(evidence, 8, 20, conflicts, False)


@pytest.mark.unit
def test_abstains_with_a_stable_message_when_evidence_is_insufficient() -> None:
    context = PackedContext((), 0, 20, (), False)
    decision = AbstentionDecider().decide(context)

    assert decision is not None
    assert decision.reason is AbstentionReason.INSUFFICIENT_EVIDENCE
    assert decision.message == (
        "I do not have enough authorized evidence to answer this question."
    )
    answer = decision.to_answer(
        Question("question-1", "What is the endpoint?"),
        AnswerUsage("none", 0, 0),
    )
    assert answer.status is VerificationStatus.ABSTAINED
    assert answer.claims == ()
    assert answer.abstention_reason is AbstentionReason.INSUFFICIENT_EVIDENCE


@pytest.mark.unit
def test_unauthorized_evidence_has_a_distinct_non_disclosing_reason() -> None:
    decision = AbstentionDecider().decide(
        PackedContext((), 0, 20, (), False), authorization_denied=True
    )

    assert decision is not None
    assert decision.reason is AbstentionReason.UNAUTHORIZED_EVIDENCE
    assert decision.message == (
        "I cannot use the evidence required to answer this question."
    )


@pytest.mark.unit
def test_irreconcilable_version_content_has_a_distinct_reason() -> None:
    context = packed(conflicts=(("document-1", ("version-1", "version-2")),))

    decision = AbstentionDecider().decide(context)

    assert decision is not None
    assert decision.reason is AbstentionReason.CONFLICTING_EVIDENCE
    assert decision.message == (
        "The authorized evidence contains an unresolved version conflict."
    )


@pytest.mark.unit
def test_verification_failure_becomes_insufficient_evidence() -> None:
    draft = GroundedAnswer(
        Question("question-1", "What is the endpoint?"),
        (Claim("Unsupported", (Citation("chunk-current", "version-2", (0, 21)),)),),
        VerificationStatus.UNVERIFIED,
        AnswerUsage("gpt-test", 4, 2),
    )
    verification = VerificationResult(draft, (VerificationFailure.UNSUPPORTED_CLAIM,))

    decision = AbstentionDecider().decide(packed(), verification=verification)

    assert decision is not None
    assert decision.reason is AbstentionReason.INSUFFICIENT_EVIDENCE


@pytest.mark.unit
def test_sufficient_non_conflicting_evidence_does_not_abstain() -> None:
    assert AbstentionDecider().decide(packed()) is None
