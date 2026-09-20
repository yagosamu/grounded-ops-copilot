"""PostgreSQL adapter for append-only redacted audit decisions."""

from sqlalchemy import Connection, text
from sqlalchemy.engine import RowMapping

from modules.audit.recorder import (
    AuditAction,
    AuditCategory,
    AuditEvent,
    AuditOutcome,
    AuditReason,
    StoredAuditQuery,
)


class PostgresAuditStore:
    """Persist events in the transaction owned by the caller."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def append(self, event: AuditEvent) -> None:
        self._connection.execute(
            text("""
                INSERT INTO audit_events (
                    event_id, occurred_at, category, action, outcome, tenant_ref,
                    actor_ref, resource_ref, correlation_ref, reason
                ) VALUES (
                    :event_id, :occurred_at, :category, :action, :outcome, :tenant_ref,
                    :actor_ref, :resource_ref, :correlation_ref, :reason
                )
            """),
            {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at,
                "category": event.category.value,
                "action": event.action.value,
                "outcome": event.outcome.value,
                "tenant_ref": event.tenant_ref,
                "actor_ref": event.actor_ref,
                "resource_ref": event.resource_ref,
                "correlation_ref": event.correlation_ref,
                "reason": event.reason.value if event.reason else None,
            },
        )

    def query(self, query: StoredAuditQuery) -> tuple[AuditEvent, ...]:
        filters = ["tenant_ref = :tenant_ref"]
        parameters: dict[str, str | int] = {
            "tenant_ref": query.tenant_ref,
            "limit": query.limit,
        }
        for column, value in (
            ("category", query.category.value if query.category else None),
            ("outcome", query.outcome.value if query.outcome else None),
            ("actor_ref", query.actor_ref),
            ("resource_ref", query.resource_ref),
            ("correlation_ref", query.correlation_ref),
        ):
            if value is not None:
                filters.append(f"{column} = :{column}")
                parameters[column] = value
        rows = self._connection.execute(
            text(f"""
                SELECT event_id, occurred_at, category, action, outcome, tenant_ref,
                       actor_ref, resource_ref, correlation_ref, reason
                FROM audit_events
                WHERE {" AND ".join(filters)}
                ORDER BY occurred_at, sequence_id
                LIMIT :limit
            """),
            parameters,
        ).mappings()
        return tuple(self._event(row) for row in rows)

    @staticmethod
    def _event(row: RowMapping) -> AuditEvent:
        return AuditEvent(
            event_id=str(row["event_id"]),
            occurred_at=row["occurred_at"],
            category=AuditCategory(row["category"]),
            action=AuditAction(row["action"]),
            outcome=AuditOutcome(row["outcome"]),
            tenant_ref=str(row["tenant_ref"]),
            actor_ref=str(row["actor_ref"]) if row["actor_ref"] else None,
            resource_ref=str(row["resource_ref"]),
            correlation_ref=str(row["correlation_ref"]),
            reason=AuditReason(row["reason"]) if row["reason"] else None,
        )
