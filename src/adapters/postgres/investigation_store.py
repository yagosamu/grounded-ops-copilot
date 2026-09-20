"""PostgreSQL checkpoint store for resumable investigation state."""

import json
from typing import cast

from pydantic import TypeAdapter
from sqlalchemy import Connection, text

from modules.investigation.workflow import (
    ConcurrentInvestigationUpdate,
    InvestigationState,
)

_STATE_ADAPTER = TypeAdapter(InvestigationState)


class PostgresInvestigationStore:
    """Persist one complete, revision-guarded snapshot after every graph node."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def create(self, state: InvestigationState) -> None:
        result = self._connection.execute(
            text("""
                INSERT INTO investigations (
                    id, tenant_id, principal_id, status, node, revision,
                    state_payload, created_at, updated_at
                ) VALUES (
                    :id, :tenant, :principal, :status, :node, :revision,
                    CAST(:payload AS jsonb), :created, :updated
                )
                ON CONFLICT (id) DO NOTHING
            """),
            self._parameters(state),
        )
        if result.rowcount != 1:
            raise ConcurrentInvestigationUpdate("investigation already exists")

    def get(self, investigation_id: str) -> InvestigationState | None:
        payload = self._connection.execute(
            text("""
                SELECT state_payload
                FROM investigations
                WHERE id=:id
            """),
            {"id": investigation_id},
        ).scalar_one_or_none()
        if payload is None:
            return None
        return _STATE_ADAPTER.validate_python(payload)

    def save(self, state: InvestigationState, expected_revision: int) -> None:
        parameters = self._parameters(state) | {"expected": expected_revision}
        result = self._connection.execute(
            text("""
                UPDATE investigations
                SET tenant_id=:tenant,
                    principal_id=:principal,
                    status=:status,
                    node=:node,
                    revision=:revision,
                    state_payload=CAST(:payload AS jsonb),
                    updated_at=:updated
                WHERE id=:id AND revision=:expected
            """),
            parameters,
        )
        if result.rowcount != 1:
            raise ConcurrentInvestigationUpdate("investigation revision changed")

    @staticmethod
    def _parameters(state: InvestigationState) -> dict[str, object]:
        payload = cast(
            dict[str, object],
            _STATE_ADAPTER.dump_python(state, mode="json"),
        )
        return {
            "id": state.id,
            "tenant": state.task.principal.tenant_id,
            "principal": state.task.principal.id,
            "status": state.status.value,
            "node": state.node.value,
            "revision": state.revision,
            "payload": json.dumps(payload, separators=(",", ":")),
            "created": state.created_at,
            "updated": state.updated_at,
        }
