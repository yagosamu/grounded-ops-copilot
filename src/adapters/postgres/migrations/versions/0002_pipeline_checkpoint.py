"""Persist the active stage for resumable bounded retries."""

from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        ALTER TABLE ingestion_jobs ADD COLUMN resume_from varchar(20);
        ALTER TABLE ingestion_jobs ADD CONSTRAINT valid_resume_from
            CHECK (resume_from IS NULL OR resume_from IN
                   ('fetching','parsing','indexing'));
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE ingestion_jobs DROP CONSTRAINT valid_resume_from;
        ALTER TABLE ingestion_jobs DROP COLUMN resume_from;
    """)
