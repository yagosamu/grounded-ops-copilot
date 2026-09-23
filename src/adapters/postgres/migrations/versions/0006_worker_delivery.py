"""Track dispatch and bounded worker deliveries independently from pipeline retries."""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ingestion_jobs
            ADD COLUMN delivery_attempts integer NOT NULL DEFAULT 0
                CHECK (delivery_attempts >= 0),
            ADD COLUMN dispatched_at timestamptz;
        CREATE INDEX ingestion_jobs_recovery_idx
            ON ingestion_jobs (dispatched_at, created_at)
            WHERE state NOT IN ('completed', 'failed');
    """)


def downgrade() -> None:
    op.execute("""
        DROP INDEX ingestion_jobs_recovery_idx;
        ALTER TABLE ingestion_jobs
            DROP COLUMN dispatched_at,
            DROP COLUMN delivery_attempts;
    """)
