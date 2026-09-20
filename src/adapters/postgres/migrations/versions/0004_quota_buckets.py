"""Shared fixed-window quota state."""

from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE quota_buckets (
            scope_key varchar(64) PRIMARY KEY,
            window_started timestamptz NOT NULL,
            request_count integer NOT NULL CHECK (request_count >= 0),
            active_count integer NOT NULL CHECK (active_count >= 0)
        );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE quota_buckets;")
