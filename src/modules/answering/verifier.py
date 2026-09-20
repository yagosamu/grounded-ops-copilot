"""Resolve proposed citations and verify deterministic claim support."""

import re
from dataclasses import dataclass
from enum import StrEnum

from domain.answering import Citation, Claim, GroundedAnswer, VerificationStatus
from modules.policy.authorizer import Principal
from modules.policy.enforcement import DocumentRef, PolicyEnforcer
from modules.retrieval.retriever import Evidence, EvidenceSet


class VerificationFailure(StrEnum):
    MISSING_CITATION = "missing_citation"
    EVIDENCE_NOT_FOUND = "evidence_not_found"
    VERSION_MISMATCH = "version_mismatch"
    SPAN_MISMATCH = "span_mismatch"
    EVIDENCE_UNAUTHORIZED = "evidence_unauthorized"
    UNSUPPORTED_CLAIM = "unsupported_claim"


@dataclass(frozen=True)
class VerificationResult:
    answer: GroundedAnswer
    failures: tuple[VerificationFailure, ...]


class CitationVerifier:
    def __init__(self, enforcer: PolicyEnforcer) -> None:
        self._enforcer = enforcer

    def verify(
        self,
        answer: GroundedAnswer,
        evidence_set: EvidenceSet,
        principal: Principal,
    ) -> VerificationResult:
        if answer.status is VerificationStatus.ABSTAINED:
            return VerificationResult(answer, ())
        failures: list[VerificationFailure] = []
        verified_claims: list[Claim] = []
        for claim in answer.claims:
            if not claim.citations:
                failures.append(VerificationFailure.MISSING_CITATION)
                continue
            resolved_evidence: list[Evidence] = []
            resolved_citations: list[Citation] = []
            for citation in claim.citations:
                resolved, failure = _resolve(citation, evidence_set.evidence)
                if failure is not None:
                    failures.append(failure)
                    continue
                assert resolved is not None
                resolved_evidence.append(resolved)
                resolved_citations.append(
                    Citation(
                        citation.evidence_id,
                        citation.document_version_id,
                        citation.span,
                        resolved=True,
                    )
                )
            if len(resolved_evidence) != len(claim.citations):
                continue
            references = tuple(
                DocumentRef(item.tenant_id, item.document_id, item.document_version_id)
                for item in resolved_evidence
            )
            try:
                authorized = self._enforcer.authorize_reads(principal, references)
            except Exception:
                failures.append(VerificationFailure.EVIDENCE_UNAUTHORIZED)
                continue
            if any(reference not in authorized for reference in references):
                failures.append(VerificationFailure.EVIDENCE_UNAUTHORIZED)
                continue
            support = set().union(*(_tokens(item.text) for item in resolved_evidence))
            if not _tokens(claim.text) <= support:
                failures.append(VerificationFailure.UNSUPPORTED_CLAIM)
                continue
            verified_claims.append(Claim(claim.text, tuple(resolved_citations)))
        unique_failures = tuple(dict.fromkeys(failures))
        if unique_failures or len(verified_claims) != len(answer.claims):
            return VerificationResult(answer, unique_failures)
        return VerificationResult(
            GroundedAnswer(
                answer.question,
                tuple(verified_claims),
                VerificationStatus.VERIFIED,
                answer.usage,
            ),
            (),
        )


def _resolve(
    citation: Citation, evidence: tuple[Evidence, ...]
) -> tuple[Evidence | None, VerificationFailure | None]:
    matching_id = tuple(
        item for item in evidence if item.chunk_id == citation.evidence_id
    )
    if not matching_id:
        return None, VerificationFailure.EVIDENCE_NOT_FOUND
    matching_version = tuple(
        item
        for item in matching_id
        if item.document_version_id == citation.document_version_id
    )
    if not matching_version:
        return None, VerificationFailure.VERSION_MISMATCH
    for item in matching_version:
        if item.span == citation.span:
            return item, None
    return None, VerificationFailure.SPAN_MISMATCH


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.casefold()))
