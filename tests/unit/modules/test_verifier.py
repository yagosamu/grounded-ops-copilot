"""Citation verification resolves exact versions and proves every claim."""

from datetime import UTC, datetime

import pytest

from adapters.policy.snapshot import SnapshotPolicyStore
from domain.answering import (
    AnswerUsage,
    Citation,
    Claim,
    GroundedAnswer,
    Question,
    VerificationStatus,
)
from modules.answering.verifier import CitationVerifier, VerificationFailure
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.retriever import Evidence, EvidenceSet


def evidence() -> Evidence:
    return Evidence(
        "chunk-1",
        "alpha",
        "source-1",
        "document-1",
        "version-2",
        0,
        "TracerProvider provides access to tracers and owns processors.",
        (5, 63),
        "hash-1",
        "markdown-v1",
        "structural-v1",
        datetime(2026, 9, 17, tzinfo=UTC),
        True,
        4.2,
        AuthorizationReason.ROLE,
    )


def answer(claim: Claim) -> GroundedAnswer:
    return GroundedAnswer(
        Question("question-1", "What provides access to tracers?"),
        (claim,),
        VerificationStatus.UNVERIFIED,
        AnswerUsage("gpt-test", 10, 5),
    )


def evidence_set() -> EvidenceSet:
    return EvidenceSet((evidence(),), "bm25", 1, 3)


PRINCIPAL = Principal("alice", "alpha", (), ())


def verifier() -> CitationVerifier:
    item = evidence()
    return CitationVerifier(
        PolicyEnforcer(
            SnapshotPolicyStore(
                (
                    DocumentPolicy(
                        item.tenant_id,
                        item.source_id,
                        item.document_id,
                        item.document_version_id,
                        ("public",),
                        False,
                    ),
                )
            )
        )
    )


class FailingPolicyStore:
    def get_document_policies(self, tenant_id, references):
        raise RuntimeError("metadata unavailable")


@pytest.mark.unit
def test_verifies_a_supported_claim_against_the_exact_versioned_span() -> None:
    draft = answer(
        Claim(
            "TracerProvider provides access to tracers.",
            (Citation("chunk-1", "version-2", (5, 63)),),
        )
    )

    result = verifier().verify(draft, evidence_set(), PRINCIPAL)

    assert result.failures == ()
    assert result.answer.status is VerificationStatus.VERIFIED
    assert result.answer.claims[0].text == draft.claims[0].text
    assert result.answer.claims[0].citations[0] == Citation(
        "chunk-1", "version-2", (5, 63), resolved=True
    )
    assert result.answer.usage == draft.usage


@pytest.mark.unit
def test_rejects_a_claim_without_a_citation() -> None:
    result = verifier().verify(
        answer(Claim("TracerProvider provides access to tracers.", ())),
        evidence_set(),
        PRINCIPAL,
    )

    assert result.answer.status is VerificationStatus.UNVERIFIED
    assert result.failures == (VerificationFailure.MISSING_CITATION,)


@pytest.mark.unit
def test_rejects_a_fabricated_evidence_identifier() -> None:
    result = verifier().verify(
        answer(
            Claim(
                "TracerProvider provides access to tracers.",
                (Citation("fabricated", "version-2", (5, 63)),),
            )
        ),
        evidence_set(),
        PRINCIPAL,
    )

    assert result.answer.status is VerificationStatus.UNVERIFIED
    assert result.failures == (VerificationFailure.EVIDENCE_NOT_FOUND,)


@pytest.mark.unit
def test_rejects_a_mismatched_document_version() -> None:
    result = verifier().verify(
        answer(
            Claim(
                "TracerProvider provides access to tracers.",
                (Citation("chunk-1", "version-1", (5, 63)),),
            )
        ),
        evidence_set(),
        PRINCIPAL,
    )

    assert result.answer.status is VerificationStatus.UNVERIFIED
    assert result.failures == (VerificationFailure.VERSION_MISMATCH,)


@pytest.mark.unit
def test_rejects_a_mismatched_span() -> None:
    result = verifier().verify(
        answer(
            Claim(
                "TracerProvider provides access to tracers.",
                (Citation("chunk-1", "version-2", (5, 45)),),
            )
        ),
        evidence_set(),
        PRINCIPAL,
    )

    assert result.answer.status is VerificationStatus.UNVERIFIED
    assert result.failures == (VerificationFailure.SPAN_MISMATCH,)


@pytest.mark.unit
def test_rejects_a_citation_that_does_not_support_the_claim() -> None:
    result = verifier().verify(
        answer(
            Claim(
                "The collector deletes every span.",
                (Citation("chunk-1", "version-2", (5, 63)),),
            )
        ),
        evidence_set(),
        PRINCIPAL,
    )

    assert result.answer.status is VerificationStatus.UNVERIFIED
    assert result.failures == (VerificationFailure.UNSUPPORTED_CLAIM,)


@pytest.mark.unit
def test_rejects_empty_evidence_without_resolving_the_citation() -> None:
    result = verifier().verify(
        answer(
            Claim(
                "TracerProvider provides access to tracers.",
                (Citation("chunk-1", "version-2", (5, 63)),),
            )
        ),
        EvidenceSet((), "bm25", 0, 1),
        PRINCIPAL,
    )

    assert result.answer.status is VerificationStatus.UNVERIFIED
    assert result.answer.claims[0].citations[0].resolved is False
    assert result.failures == (VerificationFailure.EVIDENCE_NOT_FOUND,)


@pytest.mark.unit
def test_policy_dependency_failure_cannot_reauthorize_a_citation() -> None:
    draft = answer(
        Claim(
            "TracerProvider provides access to tracers.",
            (Citation("chunk-1", "version-2", (5, 63)),),
        )
    )

    result = CitationVerifier(PolicyEnforcer(FailingPolicyStore())).verify(
        draft, evidence_set(), PRINCIPAL
    )

    assert result.answer.status is VerificationStatus.UNVERIFIED
    assert result.failures == (VerificationFailure.EVIDENCE_UNAUTHORIZED,)
