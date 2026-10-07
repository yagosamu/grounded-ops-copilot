"""Store minimal authenticated answer ratings for product evaluation."""

from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE answer_feedback (
            feedback_id varchar(32) PRIMARY KEY,
            tenant_ref varchar(64) NOT NULL,
            principal_ref varchar(64) NOT NULL,
            answer_id varchar(128) NOT NULL,
            rating varchar(16) NOT NULL CHECK (rating IN ('helpful', 'not_helpful')),
            created_at timestamptz NOT NULL DEFAULT now()
        );
        CREATE INDEX answer_feedback_tenant_created_idx
            ON answer_feedback (tenant_ref, created_at DESC);
    """)


def downgrade() -> None:
    op.execute("DROP TABLE answer_feedback;")
