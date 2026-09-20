"""Durable checkpoints for bounded investigations."""

from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE investigations (
            id varchar(128) PRIMARY KEY,
            tenant_id varchar(128) NOT NULL,
            principal_id varchar(128) NOT NULL,
            status varchar(32) NOT NULL,
            node varchar(32) NOT NULL,
            revision integer NOT NULL CHECK (revision >= 0),
            state_payload jsonb NOT NULL,
            created_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL
        );
        CREATE INDEX ix_investigations_owner
            ON investigations (tenant_id, principal_id, updated_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE investigations;")
