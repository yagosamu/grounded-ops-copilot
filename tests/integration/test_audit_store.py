"""PostgreSQL keeps redacted audit decisions durable and queryable."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from sqlalchemy import Engine, text

from adapters.postgres.audit_store import PostgresAuditStore
from modules.audit.recorder import (
    AuditCategory,
    AuditOutcome,
    AuditQuery,
    AuditReason,
    AuditRecorder,
)

pytestmark = pytest.mark.integration
STAMP = datetime(2026, 9, 20, tzinfo=UTC)


def test_persists_and_filters_required_events_without_raw_sensitive_values(
    database: Engine,
) -> None:
    sensitive = "-".join(("private", "fixture", "value"))
    tenant_id = f"tenant-{sensitive}"
    correlation_id = f"request-{sensitive}"
    key = sha256(b"integration-audit-fixture").digest()

    with database.begin() as connection:
        subject = AuditRecorder(PostgresAuditStore(connection), key)
        subject.record_authorization(
            tenant_id=tenant_id,
            principal_id=f"principal-{sensitive}",
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
            principal_id=f"principal-{sensitive}",
            investigation_id=f"investigation-{sensitive}",
            outcome=AuditOutcome.STARTED,
            correlation_id=correlation_id,
            occurred_at=STAMP,
        )

        denied = subject.query(
            AuditQuery(
                tenant_id=tenant_id,
                category=AuditCategory.AUTHORIZATION,
                outcome=AuditOutcome.DENIED,
            )
        )
        all_events = subject.query(AuditQuery(tenant_id=tenant_id))
        raw_rows = connection.execute(
            text("SELECT row_to_json(audit_events)::text FROM audit_events")
        ).scalars()
        serialized_rows = "".join(str(row) for row in raw_rows)

    assert len(denied) == 1
    assert denied[0].reason is AuditReason.CROSS_TENANT
    assert tuple(event.category for event in all_events) == (
        AuditCategory.AUTHORIZATION,
        AuditCategory.INGESTION,
        AuditCategory.INDEX_PROMOTION,
        AuditCategory.INVESTIGATION,
    )
    assert sensitive not in serialized_rows
    assert tenant_id not in serialized_rows
    assert correlation_id not in serialized_rows
