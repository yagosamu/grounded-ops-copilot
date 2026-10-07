"""Feedback survives requests without storing identity or answer text."""

import pytest
from sqlalchemy import Engine, text

from adapters.postgres.feedback_store import PostgresFeedbackStore
from modules.policy.authorizer import Principal

pytestmark = pytest.mark.integration


def test_feedback_is_durable_and_identity_is_pseudonymized(database: Engine) -> None:
    store = PostgresFeedbackStore(database, b"local-integration-redaction-key")
    principal = Principal("private-actor", "private-tenant", (), ())

    store.record(principal, "answer-123", "helpful")

    with database.connect() as connection:
        row = connection.execute(
            text(
                "SELECT tenant_ref, principal_ref, answer_id, rating "
                "FROM answer_feedback"
            )
        ).one()
    assert row.answer_id == "answer-123"
    assert row.rating == "helpful"
    assert row.tenant_ref != principal.tenant_id
    assert row.principal_ref != principal.id
    assert len(row.tenant_ref) == len(row.principal_ref) == 64


def test_feedback_migration_is_reversible(database: Engine) -> None:
    from adapters.postgres.migrations import migrate

    with database.begin() as connection:
        migrate(connection, "base")
        migrate(connection, "0006")
        assert (
            connection.execute(
                text("SELECT to_regclass('answer_feedback')")
            ).scalar_one()
            is None
        )
        migrate(connection, "head")
        assert (
            connection.execute(
                text("SELECT to_regclass('answer_feedback')")
            ).scalar_one()
            is not None
        )
