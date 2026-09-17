"""Answer outcomes measure abstentions separately from provider failures."""

import pytest

from domain.answering import AbstentionReason
from modules.answering.abstention import (
    AbstentionDecision,
    AnswerOutcome,
    measure_outcomes,
)
from modules.answering.generator import GenerationFailure


@pytest.mark.answer_eval
def test_counts_each_abstention_reason_separately_from_provider_failure() -> None:
    outcomes = (
        AnswerOutcome(
            abstention=AbstentionDecision(
                AbstentionReason.INSUFFICIENT_EVIDENCE,
                "I do not have enough authorized evidence to answer this question.",
            )
        ),
        AnswerOutcome(
            abstention=AbstentionDecision(
                AbstentionReason.UNAUTHORIZED_EVIDENCE,
                "I cannot use the evidence required to answer this question.",
            )
        ),
        AnswerOutcome(
            abstention=AbstentionDecision(
                AbstentionReason.CONFLICTING_EVIDENCE,
                "The authorized evidence contains an unresolved version conflict.",
            )
        ),
        AnswerOutcome(provider_failure=GenerationFailure.TIMEOUT),
        AnswerOutcome(),
    )

    metrics = measure_outcomes(outcomes)

    assert metrics.insufficient_evidence == 1
    assert metrics.unauthorized_evidence == 1
    assert metrics.conflicting_evidence == 1
    assert metrics.provider_failures == 1
    assert metrics.answered == 1


@pytest.mark.answer_eval
def test_rejects_an_outcome_that_conflates_abstention_and_provider_failure() -> None:
    with pytest.raises(ValueError, match="provider failure is not abstention"):
        AnswerOutcome(
            abstention=AbstentionDecision(
                AbstentionReason.INSUFFICIENT_EVIDENCE,
                "I do not have enough authorized evidence to answer this question.",
            ),
            provider_failure=GenerationFailure.UNAVAILABLE,
        )
