"""Record queryable decisions while keeping raw identifiers out of audit storage."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from secrets import token_bytes
from typing import Protocol
from uuid import uuid4


class AuditCategory(StrEnum):
    AUTHORIZATION = "authorization"
    INGESTION = "ingestion"
    INDEX_PROMOTION = "index_promotion"
    INVESTIGATION = "investigation"


class AuditAction(StrEnum):
    READ_DOCUMENT = "read_document"
    RUN_INGESTION = "run_ingestion"
    PROMOTE_INDEX_VERSION = "promote_index_version"
    RUN_INVESTIGATION = "run_investigation"


class AuditOutcome(StrEnum):
    ALLOWED = "allowed"
    DENIED = "denied"
    RETRYING = "retrying"
    COMPLETED = "completed"
    FAILED = "failed"
    DELETED = "deleted"
    PROMOTED = "promoted"
    REJECTED = "rejected"
    STARTED = "started"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class AuditReason(StrEnum):
    PUBLIC = "public"
    PRINCIPAL = "principal"
    ROLE = "role"
    GROUP = "group"
    POLICY_DENIED = "policy_denied"
    UNKNOWN_PRINCIPAL = "unknown_principal"
    CROSS_TENANT = "cross_tenant"
    ACTION_DENIED = "action_denied"


@dataclass(frozen=True)
class AuditEvent:
    event_id: str
    occurred_at: datetime
    category: AuditCategory
    action: AuditAction
    outcome: AuditOutcome
    tenant_ref: str
    actor_ref: str | None
    resource_ref: str
    correlation_ref: str
    reason: AuditReason | None = None


@dataclass(frozen=True)
class StoredAuditQuery:
    tenant_ref: str
    category: AuditCategory | None = None
    outcome: AuditOutcome | None = None
    actor_ref: str | None = None
    resource_ref: str | None = None
    correlation_ref: str | None = None
    limit: int = 100


@dataclass(frozen=True, repr=False)
class AuditQuery:
    tenant_id: str
    category: AuditCategory | None = None
    outcome: AuditOutcome | None = None
    actor_id: str | None = None
    resource_id: str | None = None
    correlation_id: str | None = None
    limit: int = 100

    def __post_init__(self) -> None:
        if not 1 <= self.limit <= 1000:
            raise ValueError("audit query limit must be between 1 and 1000")


class AuditStore(Protocol):
    def append(self, event: AuditEvent) -> None: ...

    def query(self, query: StoredAuditQuery) -> tuple[AuditEvent, ...]: ...


class InMemoryAuditStore:
    """Local audit sink used when no durable composition has been supplied."""

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []

    def append(self, event: AuditEvent) -> None:
        self._events.append(event)

    def query(self, query: StoredAuditQuery) -> tuple[AuditEvent, ...]:
        matches = (
            event
            for event in self._events
            if event.tenant_ref == query.tenant_ref
            and (query.category is None or event.category is query.category)
            and (query.outcome is None or event.outcome is query.outcome)
            and (query.actor_ref is None or event.actor_ref == query.actor_ref)
            and (query.resource_ref is None or event.resource_ref == query.resource_ref)
            and (
                query.correlation_ref is None
                or event.correlation_ref == query.correlation_ref
            )
        )
        return tuple(list(matches)[: query.limit])


class AuditRecorder:
    """Create a strict event envelope and pseudonymize every caller identifier."""

    def __init__(self, store: AuditStore, redaction_key: bytes) -> None:
        if not redaction_key:
            raise ValueError("redaction key must not be empty")
        self._store = store
        self._redaction_key = redaction_key

    @classmethod
    def ephemeral(cls) -> AuditRecorder:
        """Keep safe in-process events for local/offline compositions."""
        return cls(InMemoryAuditStore(), token_bytes(32))

    def record_authorization(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        document_id: str,
        allowed: bool,
        reason: AuditReason,
        correlation_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        return self._record(
            tenant_id=tenant_id,
            actor_id=principal_id,
            resource_id=document_id,
            correlation_id=correlation_id,
            category=AuditCategory.AUTHORIZATION,
            action=AuditAction.READ_DOCUMENT,
            outcome=AuditOutcome.ALLOWED if allowed else AuditOutcome.DENIED,
            reason=reason,
            occurred_at=occurred_at,
        )

    def record_ingestion(
        self,
        *,
        tenant_id: str,
        job_id: str,
        outcome: AuditOutcome,
        correlation_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        self._require_outcome(
            outcome,
            {
                AuditOutcome.RETRYING,
                AuditOutcome.COMPLETED,
                AuditOutcome.FAILED,
                AuditOutcome.DELETED,
            },
        )
        return self._record(
            tenant_id=tenant_id,
            actor_id=None,
            resource_id=job_id,
            correlation_id=correlation_id,
            category=AuditCategory.INGESTION,
            action=AuditAction.RUN_INGESTION,
            outcome=outcome,
            reason=None,
            occurred_at=occurred_at,
        )

    def record_index_promotion(
        self,
        *,
        tenant_id: str,
        version_id: str,
        promoted: bool,
        correlation_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        return self._record(
            tenant_id=tenant_id,
            actor_id=None,
            resource_id=version_id,
            correlation_id=correlation_id,
            category=AuditCategory.INDEX_PROMOTION,
            action=AuditAction.PROMOTE_INDEX_VERSION,
            outcome=AuditOutcome.PROMOTED if promoted else AuditOutcome.REJECTED,
            reason=None,
            occurred_at=occurred_at,
        )

    def record_investigation(
        self,
        *,
        tenant_id: str,
        principal_id: str,
        investigation_id: str,
        outcome: AuditOutcome,
        correlation_id: str | None = None,
        occurred_at: datetime | None = None,
    ) -> AuditEvent:
        self._require_outcome(
            outcome,
            {
                AuditOutcome.STARTED,
                AuditOutcome.COMPLETED,
                AuditOutcome.FAILED,
                AuditOutcome.PARTIAL,
                AuditOutcome.CANCELLED,
            },
        )
        return self._record(
            tenant_id=tenant_id,
            actor_id=principal_id,
            resource_id=investigation_id,
            correlation_id=correlation_id,
            category=AuditCategory.INVESTIGATION,
            action=AuditAction.RUN_INVESTIGATION,
            outcome=outcome,
            reason=None,
            occurred_at=occurred_at,
        )

    def query(self, query: AuditQuery) -> tuple[AuditEvent, ...]:
        return self._store.query(
            StoredAuditQuery(
                tenant_ref=self._fingerprint("tenant", query.tenant_id),
                category=query.category,
                outcome=query.outcome,
                actor_ref=(
                    self._fingerprint("actor", query.actor_id)
                    if query.actor_id is not None
                    else None
                ),
                resource_ref=(
                    self._fingerprint("resource", query.resource_id)
                    if query.resource_id is not None
                    else None
                ),
                correlation_ref=(
                    self._fingerprint("correlation", query.correlation_id)
                    if query.correlation_id is not None
                    else None
                ),
                limit=query.limit,
            )
        )

    def _record(
        self,
        *,
        tenant_id: str,
        actor_id: str | None,
        resource_id: str,
        correlation_id: str | None,
        category: AuditCategory,
        action: AuditAction,
        outcome: AuditOutcome,
        reason: AuditReason | None,
        occurred_at: datetime | None,
    ) -> AuditEvent:
        correlation = correlation_id or uuid4().hex
        event = AuditEvent(
            event_id=uuid4().hex,
            occurred_at=occurred_at or datetime.now(UTC),
            category=category,
            action=action,
            outcome=outcome,
            tenant_ref=self._fingerprint("tenant", tenant_id),
            actor_ref=(
                self._fingerprint("actor", actor_id) if actor_id is not None else None
            ),
            resource_ref=self._fingerprint("resource", resource_id),
            correlation_ref=self._fingerprint("correlation", correlation),
            reason=reason,
        )
        self._store.append(event)
        return event

    def _fingerprint(self, field: str, value: str) -> str:
        if not value:
            raise ValueError(f"audit {field} must not be empty")
        return hmac.new(
            self._redaction_key,
            f"{field}\0{value}".encode(),
            sha256,
        ).hexdigest()

    @staticmethod
    def _require_outcome(outcome: AuditOutcome, allowed: set[AuditOutcome]) -> None:
        if outcome not in allowed:
            raise ValueError("invalid audit outcome for category")
