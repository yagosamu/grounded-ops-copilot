"""Choose explicit abstention outcomes from authorized evidence signals."""

from dataclasses import dataclass

from domain.answering import (
    AbstentionReason,
    AnswerUsage,
    GroundedAnswer,
    Question,
    VerificationStatus,
)
from modules.answering.context_packer import PackedContext
from modules.answering.generator import GenerationFailure
from modules.answering.verifier import VerificationResult

_MESSAGES = {
    AbstentionReason.INSUFFICIENT_EVIDENCE: (
        "I do not have enough authorized evidence to answer this question."
    ),
    AbstentionReason.UNAUTHORIZED_EVIDENCE: (
        "I cannot use the evidence required to answer this question."
    ),
    AbstentionReason.CONFLICTING_EVIDENCE: (
        "The authorized evidence contains an unresolved version conflict."
    ),
}


@dataclass(frozen=True)
class AbstentionDecision:
    reason: AbstentionReason
    message: str

    def __post_init__(self) -> None:
        if self.message != _MESSAGES[self.reason]:
            raise ValueError("invalid abstention message")

    def to_answer(self, question: Question, usage: AnswerUsage) -> GroundedAnswer:
        return GroundedAnswer(
            question,
            (),
            VerificationStatus.ABSTAINED,
            usage,
            self.reason,
        )


class AbstentionDecider:
    def decide(
        self,
        context: PackedContext,
        *,
        verification: VerificationResult | None = None,
        authorization_denied: bool = False,
    ) -> AbstentionDecision | None:
        if authorization_denied:
            return _decision(AbstentionReason.UNAUTHORIZED_EVIDENCE)
        if not context.evidence:
            return _decision(AbstentionReason.INSUFFICIENT_EVIDENCE)
        if _has_irreconcilable_conflict(context):
            return _decision(AbstentionReason.CONFLICTING_EVIDENCE)
        if verification is not None and verification.failures:
            return _decision(AbstentionReason.INSUFFICIENT_EVIDENCE)
        return None


@dataclass(frozen=True)
class AnswerOutcome:
    abstention: AbstentionDecision | None = None
    provider_failure: GenerationFailure | None = None

    def __post_init__(self) -> None:
        if self.abstention is not None and self.provider_failure is not None:
            raise ValueError("provider failure is not abstention")


@dataclass(frozen=True)
class OutcomeMetrics:
    insufficient_evidence: int
    unauthorized_evidence: int
    conflicting_evidence: int
    provider_failures: int
    answered: int


def measure_outcomes(outcomes: tuple[AnswerOutcome, ...]) -> OutcomeMetrics:
    return OutcomeMetrics(
        sum(
            outcome.abstention is not None
            and outcome.abstention.reason is AbstentionReason.INSUFFICIENT_EVIDENCE
            for outcome in outcomes
        ),
        sum(
            outcome.abstention is not None
            and outcome.abstention.reason is AbstentionReason.UNAUTHORIZED_EVIDENCE
            for outcome in outcomes
        ),
        sum(
            outcome.abstention is not None
            and outcome.abstention.reason is AbstentionReason.CONFLICTING_EVIDENCE
            for outcome in outcomes
        ),
        sum(outcome.provider_failure is not None for outcome in outcomes),
        sum(
            outcome.abstention is None and outcome.provider_failure is None
            for outcome in outcomes
        ),
    )


def _decision(reason: AbstentionReason) -> AbstentionDecision:
    return AbstentionDecision(reason, _MESSAGES[reason])


def _has_irreconcilable_conflict(context: PackedContext) -> bool:
    for document_id, _versions in context.conflicts:
        texts = {
            " ".join(item.text.casefold().split())
            for item in context.evidence
            if item.document_id == document_id
        }
        if len(texts) > 1:
            return True
    return False
