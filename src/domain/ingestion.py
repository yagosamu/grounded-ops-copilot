"""Immutable ingestion identities and guarded lifecycle transitions."""

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum


def validate_identifier(value: str) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", value):
        raise ValueError("invalid identifier")


@dataclass(frozen=True)
class Source:
    id: str
    tenant_id: str
    type: str
    external_ref: str
    policy: tuple[str, ...]
    cursor: str | None = None

    def __post_init__(self) -> None:
        for value in (self.id, self.tenant_id, self.type):
            validate_identifier(value)
        if not self.policy or len(self.policy) > 100:
            raise ValueError("invalid access policy")
        for value in self.policy:
            validate_identifier(value)
        if not self.external_ref or len(self.external_ref) > 2048:
            raise ValueError("invalid external reference")


@dataclass(frozen=True)
class Document:
    id: str
    tenant_id: str
    source_id: str
    canonical_key: str
    current_version_id: str | None = None
    deleted: bool = False

    def __post_init__(self) -> None:
        for value in (self.id, self.tenant_id, self.source_id):
            validate_identifier(value)
        if not self.canonical_key or len(self.canonical_key) > 2048:
            raise ValueError("invalid canonical key")

    def delete(self) -> "Document":
        return replace(self, deleted=True, current_version_id=None)


@dataclass(frozen=True)
class DocumentVersion:
    id: str
    tenant_id: str
    document_id: str
    source_version: str
    content_hash: str
    source_timestamp: datetime
    parser_version: str
    raw_ref: str | None = None
    normalized_ref: str | None = None

    def __post_init__(self) -> None:
        for value in (
            self.id,
            self.tenant_id,
            self.document_id,
            self.source_version,
            self.parser_version,
        ):
            validate_identifier(value)
        if not re.fullmatch("[a-f0-9]{64}", self.content_hash):
            raise ValueError("invalid content hash")
        if self.source_timestamp.tzinfo is None:
            raise ValueError("source timestamp requires timezone")


class JobState(StrEnum):
    QUEUED = "queued"
    FETCHING = "fetching"
    PARSING = "parsing"
    INDEXING = "indexing"
    COMPLETED = "completed"
    RETRYING = "retrying"
    FAILED = "failed"


class InvalidTransition(ValueError):
    """Expose a content-free event that an application can persist for audit."""

    def __init__(self, job: "IngestionJob", target: JobState) -> None:
        super().__init__("invalid ingestion transition")
        self.event = {
            "event": "invalid_ingestion_transition",
            "job_id": job.id,
            "tenant_id": job.tenant_id,
            "from": job.state.value,
            "to": target.value,
        }


@dataclass(frozen=True)
class IngestionJob:
    id: str
    tenant_id: str
    idempotency_key: str
    state: JobState = JobState.QUEUED
    attempts: int = 0
    error_class: str | None = None

    def __post_init__(self) -> None:
        for value in (self.id, self.tenant_id, self.idempotency_key):
            validate_identifier(value)
        if self.attempts < 0:
            raise ValueError("attempts cannot be negative")

    def transition(self, target: JobState) -> "IngestionJob":
        permitted = {
            JobState.QUEUED: {JobState.FETCHING},
            JobState.FETCHING: {JobState.PARSING, JobState.RETRYING},
            JobState.PARSING: {JobState.INDEXING, JobState.RETRYING},
            JobState.INDEXING: {JobState.COMPLETED, JobState.RETRYING},
            JobState.RETRYING: {
                JobState.FETCHING,
                JobState.PARSING,
                JobState.INDEXING,
                JobState.FAILED,
            },
            JobState.COMPLETED: set(),
            JobState.FAILED: set(),
        }
        if target not in permitted[self.state]:
            raise InvalidTransition(self, target)
        return replace(self, state=target)
