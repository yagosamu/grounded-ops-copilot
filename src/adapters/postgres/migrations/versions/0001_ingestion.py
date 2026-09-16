"""Tenant-scoped sources, immutable versions and idempotent jobs."""

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE sources (
            tenant_id varchar(128) NOT NULL, id varchar(128) NOT NULL,
            type varchar(128) NOT NULL, external_ref text NOT NULL,
            policy jsonb NOT NULL, cursor text,
            PRIMARY KEY (tenant_id, id)
        );
        CREATE TABLE documents (
            tenant_id varchar(128) NOT NULL, id varchar(128) NOT NULL,
            source_id varchar(128) NOT NULL, canonical_key text NOT NULL,
            current_version_id varchar(128), deleted boolean NOT NULL DEFAULT false,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, source_id, canonical_key),
            FOREIGN KEY (tenant_id, source_id) REFERENCES sources(tenant_id, id)
        );
        CREATE TABLE document_versions (
            tenant_id varchar(128) NOT NULL, id varchar(128) NOT NULL,
            document_id varchar(128) NOT NULL, source_version varchar(128) NOT NULL,
            content_hash varchar(64) NOT NULL, source_timestamp timestamptz NOT NULL,
            parser_version varchar(128) NOT NULL, raw_ref text, normalized_ref text,
            PRIMARY KEY (tenant_id, id),
            UNIQUE (tenant_id, document_id, source_version),
            UNIQUE (tenant_id, document_id, id),
            FOREIGN KEY (tenant_id, document_id) REFERENCES documents(tenant_id, id)
        );
        ALTER TABLE documents ADD CONSTRAINT current_version_fk
            FOREIGN KEY (tenant_id, id, current_version_id)
            REFERENCES document_versions(tenant_id, document_id, id);
        CREATE TABLE ingestion_jobs (
            tenant_id varchar(128) NOT NULL, id varchar(128) NOT NULL,
            version_id varchar(128) NOT NULL, idempotency_key varchar(128) NOT NULL,
            state varchar(20) NOT NULL DEFAULT 'queued',
            attempts integer NOT NULL DEFAULT 0 CHECK (attempts >= 0),
            error_class varchar(128),
            created_at timestamptz NOT NULL DEFAULT now(),
            updated_at timestamptz NOT NULL DEFAULT now(),
            PRIMARY KEY (tenant_id, id), UNIQUE (tenant_id, idempotency_key),
            FOREIGN KEY (tenant_id, version_id)
                REFERENCES document_versions(tenant_id, id),
            CHECK (state IN ('queued','fetching','parsing','indexing',
                            'retrying','completed','failed'))
        );
    """)


def downgrade() -> None:
    op.execute("""
        DROP TABLE ingestion_jobs;
        ALTER TABLE documents DROP CONSTRAINT current_version_fk;
        DROP TABLE document_versions;
        DROP TABLE documents;
        DROP TABLE sources;
    """)
