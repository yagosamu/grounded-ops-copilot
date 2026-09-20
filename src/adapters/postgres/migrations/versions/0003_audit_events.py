"""Append-only redacted security decisions."""

from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE audit_events (
            sequence_id bigserial UNIQUE NOT NULL,
            event_id varchar(32) PRIMARY KEY,
            occurred_at timestamptz NOT NULL,
            category varchar(32) NOT NULL,
            action varchar(32) NOT NULL,
            outcome varchar(20) NOT NULL,
            tenant_ref varchar(64) NOT NULL,
            actor_ref varchar(64),
            resource_ref varchar(64) NOT NULL,
            correlation_ref varchar(64) NOT NULL,
            reason varchar(32),
            CHECK (category IN (
                'authorization','ingestion','index_promotion','investigation'
            )),
            CHECK (outcome IN (
                'allowed','denied','retrying','completed','failed','deleted',
                'promoted','rejected','started','partial','cancelled'
            ))
        );
        CREATE INDEX audit_events_lookup
            ON audit_events (tenant_ref, category, occurred_at DESC);
        REVOKE UPDATE, DELETE ON audit_events FROM PUBLIC;
    """)


def downgrade() -> None:
    op.execute("DROP TABLE audit_events;")
