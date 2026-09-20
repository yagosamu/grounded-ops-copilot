"""SEC-01/02 audit decisions remain queryable without raw sensitive values."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from modules.audit.recorder import (
    AuditAction,
    AuditCategory,
    AuditOutcome,
    AuditQuery,
    AuditReason,
    AuditRecorder,
    InMemoryAuditStore,
)

pytestmark = pytest.mark.unit
STAMP = datetime(2026, 9, 20, tzinfo=UTC)


def recorder() -> AuditRecorder:
    return AuditRecorder(
        InMemoryAuditStore(), sha256(b"deterministic-audit-fixture").digest()
    )


def test_records_every_required_decision_as_queryable_redacted_fields() -> None:
    subject = recorder()
    sensitive = "-".join(("private", "fixture", "value"))
    tenant_id = f"tenant-{sensitive}"
    principal_id = f"principal-{sensitive}"
    correlation_id = f"request-{sensitive}"

    subject.record_authorization(
        tenant_id=tenant_id,
        principal_id=principal_id,
        document_id=f"document-{sensitive}",
        allowed=False,
        reason=AuditReason.CROSS_TENANT,
        correlation_id=correlation_id,
        occurred_at=STAMP,
    )
    subject.record_ingestion(
        tenant_id=tenant_id,
        job_id=f"job-{sensitive}",
        outcome=AuditOutcome.COMPLETED,
        correlation_id=correlation_id,
        occurred_at=STAMP,
    )
    subject.record_index_promotion(
        tenant_id=tenant_id,
        version_id=f"version-{sensitive}",
        promoted=True,
        correlation_id=correlation_id,
        occurred_at=STAMP,
    )
    subject.record_investigation(
        tenant_id=tenant_id,
        principal_id=principal_id,
        investigation_id=f"investigation-{sensitive}",
        outcome=AuditOutcome.STARTED,
        correlation_id=correlation_id,
        occurred_at=STAMP,
    )

    events = subject.query(AuditQuery(tenant_id=tenant_id))

    assert tuple(event.category for event in events) == (
        AuditCategory.AUTHORIZATION,
        AuditCategory.INGESTION,
        AuditCategory.INDEX_PROMOTION,
        AuditCategory.INVESTIGATION,
    )
    assert tuple(event.outcome for event in events) == (
        AuditOutcome.DENIED,
        AuditOutcome.COMPLETED,
        AuditOutcome.PROMOTED,
        AuditOutcome.STARTED,
    )
    assert tuple(event.action for event in events) == (
        AuditAction.READ_DOCUMENT,
        AuditAction.RUN_INGESTION,
        AuditAction.PROMOTE_INDEX_VERSION,
        AuditAction.RUN_INVESTIGATION,
    )
    assert events[0].reason is AuditReason.CROSS_TENANT
    assert all(event.reason is None for event in events[1:])
    assert all(event.occurred_at == STAMP for event in events)
    assert len({event.event_id for event in events}) == 4
    assert all(len(event.event_id) == 32 for event in events)
    assert len({event.tenant_ref for event in events}) == 1
    assert all(len(event.tenant_ref) == 64 for event in events)
    assert all(len(event.resource_ref) == 64 for event in events)
    assert len({event.correlation_ref for event in events}) == 1
    assert all(len(event.correlation_ref) == 64 for event in events)
    assert events[0].actor_ref == events[3].actor_ref
    assert events[1].actor_ref is None
    assert events[2].actor_ref is None
    assert sensitive not in repr(events)
    assert tenant_id not in repr(events)
    assert principal_id not in repr(events)
    assert correlation_id not in repr(events)


def test_queries_by_raw_resource_and_correlation_without_persisting_them() -> None:
    subject = recorder()
    subject.record_ingestion(
        tenant_id="alpha",
        job_id="job-1",
        outcome=AuditOutcome.FAILED,
        correlation_id="request-1",
        occurred_at=STAMP,
    )
    subject.record_ingestion(
        tenant_id="alpha",
        job_id="job-2",
        outcome=AuditOutcome.COMPLETED,
        correlation_id="request-2",
        occurred_at=STAMP,
    )

    failed = subject.query(
        AuditQuery(
            tenant_id="alpha",
            category=AuditCategory.INGESTION,
            outcome=AuditOutcome.FAILED,
            resource_id="job-1",
            correlation_id="request-1",
        )
    )
    foreign = subject.query(AuditQuery(tenant_id="beta"))

    assert len(failed) == 1
    assert failed[0].outcome is AuditOutcome.FAILED
    assert foreign == ()


def test_rejects_an_empty_redaction_key() -> None:
    with pytest.raises(ValueError, match="redaction key"):
        AuditRecorder(InMemoryAuditStore(), b"")
