"""Public contracts for grounded claims, citations, and answer state."""

from dataclasses import dataclass
from enum import StrEnum


class VerificationStatus(StrEnum):
    UNVERIFIED = "unverified"
    VERIFIED = "verified"
    ABSTAINED = "abstained"


class AbstentionReason(StrEnum):
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    UNAUTHORIZED_EVIDENCE = "unauthorized_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"


@dataclass(frozen=True)
class Question:
    id: str
    text: str

    def __post_init__(self) -> None:
        if not self.id.strip() or not self.text.strip() or len(self.text) > 1000:
            raise ValueError("invalid question")


@dataclass(frozen=True)
class Citation:
    evidence_id: str
    document_version_id: str
    span: tuple[int, int]
    resolved: bool = False

    def __post_init__(self) -> None:
        start, end = self.span
        if not self.evidence_id.strip() or not self.document_version_id.strip():
            raise ValueError("invalid citation identity")
        if start < 0 or end <= start:
            raise ValueError("invalid citation span")


@dataclass(frozen=True)
class Claim:
    text: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("invalid claim")


@dataclass(frozen=True)
class AnswerUsage:
    model: str
    input_tokens: int
    output_tokens: int

    def __post_init__(self) -> None:
        if not self.model.strip() or self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("invalid answer usage")


@dataclass(frozen=True)
class GroundedAnswer:
    question: Question
    claims: tuple[Claim, ...]
    status: VerificationStatus
    usage: AnswerUsage
    abstention_reason: AbstentionReason | None = None

    def __post_init__(self) -> None:
        if self.status is VerificationStatus.VERIFIED:
            if not self.claims:
                raise ValueError("verified answer requires claims")
            if any(not claim.citations for claim in self.claims):
                raise ValueError("verified claim requires a citation")
            if any(
                not citation.resolved
                for claim in self.claims
                for citation in claim.citations
            ):
                raise ValueError("verified answer requires resolved citations")
        if self.status is VerificationStatus.ABSTAINED:
            if self.abstention_reason is None:
                raise ValueError("abstained answer requires a reason")
            if self.claims:
                raise ValueError("abstained answer cannot contain claims")
        elif self.abstention_reason is not None:
            raise ValueError("reason requires abstained status")
