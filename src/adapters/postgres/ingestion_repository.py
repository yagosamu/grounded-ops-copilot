"""Transactional ingestion repository; every lookup requires tenant context."""

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from uuid import uuid4

from sqlalchemy import Connection, bindparam, text
from sqlalchemy.engine import RowMapping

from domain.ingestion import Document, DocumentVersion, IngestionJob, JobState, Source
from modules.policy.enforcement import DocumentPolicy, DocumentRef


@dataclass(frozen=True)
class Submission:
    document: Document
    version: DocumentVersion
    job: IngestionJob


class IngestionRepository:
    """The caller owns commit/rollback through an SQLAlchemy transaction."""

    def __init__(self, connection: Connection) -> None:
        self.connection = connection

    def submit(
        self,
        source: Source,
        canonical_key: str,
        source_version: str,
        content_hash: str,
        source_timestamp: datetime,
        parser_version: str,
    ) -> Submission:
        document = Document(uuid4().hex, source.tenant_id, source.id, canonical_key)
        candidate = DocumentVersion(
            uuid4().hex,
            source.tenant_id,
            document.id,
            source_version,
            content_hash,
            source_timestamp,
            parser_version,
        )
        parameters = {
            "tenant": source.tenant_id,
            "source": source.id,
            "type": source.type,
            "ref": source.external_ref,
            "policy": json.dumps(source.policy),
            "cursor": source.cursor,
            "key": canonical_key,
            "document": document.id,
        }
        self.connection.execute(
            text("""
            INSERT INTO sources (tenant_id,id,type,external_ref,policy,cursor)
            VALUES (:tenant,:source,:type,:ref,CAST(:policy AS jsonb),:cursor)
            ON CONFLICT (tenant_id,id) DO UPDATE SET policy=EXCLUDED.policy
        """),
            parameters,
        )
        self.connection.execute(
            text("""
            INSERT INTO documents (tenant_id,id,source_id,canonical_key)
            VALUES (:tenant,:document,:source,:key)
            ON CONFLICT (tenant_id,source_id,canonical_key) DO NOTHING
        """),
            parameters,
        )
        row = (
            self.connection.execute(
                text("""
            SELECT * FROM documents WHERE tenant_id=:tenant AND source_id=:source
            AND canonical_key=:key FOR UPDATE
        """),
                parameters,
            )
            .mappings()
            .one()
        )
        document = Document(**dict(row))
        version_parameters = {
            "tenant": source.tenant_id,
            "document": document.id,
            "id": candidate.id,
            "source_version": source_version,
            "hash": content_hash,
            "stamp": source_timestamp,
            "parser": parser_version,
        }
        self.connection.execute(
            text("""
            INSERT INTO document_versions
                (tenant_id,id,document_id,source_version,content_hash,
                 source_timestamp,parser_version)
            VALUES (:tenant,:id,:document,:source_version,:hash,:stamp,:parser)
            ON CONFLICT (tenant_id,document_id,source_version) DO NOTHING
        """),
            version_parameters,
        )
        row = (
            self.connection.execute(
                text("""
            SELECT * FROM document_versions WHERE tenant_id=:tenant
            AND document_id=:document AND source_version=:source_version
        """),
                version_parameters,
            )
            .mappings()
            .one()
        )
        version = DocumentVersion(**dict(row))
        if version.content_hash != content_hash:
            raise ValueError("source version content conflict")
        key = sha256(f"{document.id}:{source_version}".encode()).hexdigest()
        self.connection.execute(
            text("""
            INSERT INTO ingestion_jobs (tenant_id,id,version_id,idempotency_key)
            VALUES (:tenant,:id,:version,:key)
            ON CONFLICT (tenant_id,idempotency_key) DO NOTHING
        """),
            {
                "tenant": source.tenant_id,
                "id": uuid4().hex,
                "version": version.id,
                "key": key,
            },
        )
        row = (
            self.connection.execute(
                text("""
            SELECT id,tenant_id,idempotency_key,state,attempts,error_class,resume_from
            FROM ingestion_jobs WHERE tenant_id=:tenant AND idempotency_key=:key
        """),
                {"tenant": source.tenant_id, "key": key},
            )
            .mappings()
            .one()
        )
        return Submission(
            document,
            version,
            self._job(row),
        )

    def get_source(self, tenant_id: str, source_id: str) -> Source | None:
        row = (
            self.connection.execute(
                text("""
            SELECT * FROM sources WHERE tenant_id=:tenant AND id=:id
        """),
                {"tenant": tenant_id, "id": source_id},
            )
            .mappings()
            .one_or_none()
        )
        return Source(**dict(row) | {"policy": tuple(row["policy"])}) if row else None

    def get_document(self, tenant_id: str, document_id: str) -> Document | None:
        row = (
            self.connection.execute(
                text("""
            SELECT * FROM documents WHERE tenant_id=:tenant AND id=:id
        """),
                {"tenant": tenant_id, "id": document_id},
            )
            .mappings()
            .one_or_none()
        )
        return Document(**dict(row)) if row else None

    def get_document_policies(
        self, tenant_id: str, references: tuple[DocumentRef, ...]
    ) -> dict[DocumentRef, DocumentPolicy]:
        scoped = tuple(
            dict.fromkeys(
                reference
                for reference in references
                if reference.tenant_id == tenant_id
            )
        )
        if not scoped:
            return {}
        document_ids = tuple(
            dict.fromkeys(reference.document_id for reference in scoped)
        )
        document_rows = self.connection.execute(
            text("""
            SELECT d.id AS document_id,d.source_id,d.deleted,s.policy
            FROM documents d
            JOIN sources s ON s.tenant_id=d.tenant_id AND s.id=d.source_id
            WHERE d.tenant_id=:tenant AND d.id IN :document_ids
        """).bindparams(bindparam("document_ids", expanding=True)),
            {"tenant": tenant_id, "document_ids": document_ids},
        ).mappings()
        documents = {str(row["document_id"]): row for row in document_rows}
        version_ids = tuple(
            dict.fromkeys(
                reference.document_version_id
                for reference in scoped
                if reference.document_version_id is not None
            )
        )
        versions: set[tuple[str, str]] = set()
        if version_ids:
            version_rows = self.connection.execute(
                text("""
                SELECT document_id,id FROM document_versions
                WHERE tenant_id=:tenant AND id IN :version_ids
            """).bindparams(bindparam("version_ids", expanding=True)),
                {"tenant": tenant_id, "version_ids": version_ids},
            ).mappings()
            versions = {
                (str(row["document_id"]), str(row["id"])) for row in version_rows
            }
        resolved: dict[DocumentRef, DocumentPolicy] = {}
        for reference in scoped:
            row = documents.get(reference.document_id)
            version_id = reference.document_version_id
            if row is None or (
                version_id is not None
                and (reference.document_id, version_id) not in versions
            ):
                continue
            resolved[reference] = DocumentPolicy(
                tenant_id,
                str(row["source_id"]),
                reference.document_id,
                version_id,
                tuple(str(entry) for entry in row["policy"]),
                bool(row["deleted"]),
            )
        return resolved

    def get_version(self, tenant_id: str, version_id: str) -> DocumentVersion | None:
        row = (
            self.connection.execute(
                text("""
            SELECT * FROM document_versions WHERE tenant_id=:tenant AND id=:id
        """),
                {"tenant": tenant_id, "id": version_id},
            )
            .mappings()
            .one_or_none()
        )
        return DocumentVersion(**dict(row)) if row else None

    def get_job(self, tenant_id: str, job_id: str) -> IngestionJob | None:
        row = (
            self.connection.execute(
                text("""
            SELECT id,tenant_id,idempotency_key,state,attempts,error_class,resume_from
            FROM ingestion_jobs WHERE tenant_id=:tenant AND id=:id
        """),
                {"tenant": tenant_id, "id": job_id},
            )
            .mappings()
            .one_or_none()
        )
        return self._job(row) if row else None

    def list_versions(self, tenant_id: str, document_id: str) -> list[DocumentVersion]:
        rows = self.connection.execute(
            text("""
            SELECT * FROM document_versions WHERE tenant_id=:tenant
            AND document_id=:id ORDER BY source_timestamp, source_version
        """),
            {"tenant": tenant_id, "id": document_id},
        ).mappings()
        return [DocumentVersion(**dict(row)) for row in rows]

    def get_document_by_key(
        self, tenant_id: str, source_id: str, canonical_key: str
    ) -> Document | None:
        row = (
            self.connection.execute(
                text("""
            SELECT * FROM documents WHERE tenant_id=:tenant AND source_id=:source
            AND canonical_key=:key
        """),
                {"tenant": tenant_id, "source": source_id, "key": canonical_key},
            )
            .mappings()
            .one_or_none()
        )
        return Document(**dict(row)) if row else None

    def save_job(self, job: IngestionJob) -> IngestionJob:
        result = self.connection.execute(
            text("""
            UPDATE ingestion_jobs SET state=:state, attempts=:attempts,
                error_class=:error, resume_from=:resume, updated_at=now()
            WHERE tenant_id=:tenant AND id=:id
        """),
            {
                "tenant": job.tenant_id,
                "id": job.id,
                "state": job.state.value,
                "attempts": job.attempts,
                "error": job.error_class,
                "resume": job.resume_from.value if job.resume_from else None,
            },
        )
        if result.rowcount != 1:
            raise ValueError("unknown ingestion job")
        return job

    def update_artifacts(
        self,
        tenant_id: str,
        version_id: str,
        raw_ref: str,
        normalized_ref: str,
    ) -> DocumentVersion:
        row = (
            self.connection.execute(
                text("""
            UPDATE document_versions
            SET raw_ref=COALESCE(raw_ref,:raw),
                normalized_ref=COALESCE(normalized_ref,:normalized)
            WHERE tenant_id=:tenant AND id=:id
              AND (raw_ref IS NULL OR raw_ref=:raw)
              AND (normalized_ref IS NULL OR normalized_ref=:normalized)
            RETURNING *
        """),
                {
                    "tenant": tenant_id,
                    "id": version_id,
                    "raw": raw_ref,
                    "normalized": normalized_ref,
                },
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            raise ValueError("artifact reference conflict")
        return DocumentVersion(**dict(row))

    def promote(self, tenant_id: str, version_id: str) -> None:
        version = self.get_version(tenant_id, version_id)
        if version is None:
            raise ValueError("unknown document version")
        self.connection.execute(
            text("""
            SELECT id FROM documents WHERE tenant_id=:tenant AND id=:document
            FOR UPDATE
        """),
            {"tenant": tenant_id, "document": version.document_id},
        )
        self.connection.execute(
            text("""
            UPDATE documents SET current_version_id=:version
            WHERE tenant_id=:tenant AND id=:document AND NOT deleted AND (
                current_version_id IS NULL OR EXISTS (
                    SELECT 1 FROM document_versions current
                    WHERE current.tenant_id=:tenant AND current.id=current_version_id
                    AND (current.source_timestamp, current.source_version)
                        <= (:stamp,:revision)
                )
            )
        """),
            {
                "tenant": tenant_id,
                "document": version.document_id,
                "version": version.id,
                "stamp": version.source_timestamp,
                "revision": version.source_version,
            },
        )

    def delete(self, tenant_id: str, document_id: str) -> None:
        self.connection.execute(
            text("""
            UPDATE documents SET current_version_id=NULL, deleted=true
            WHERE tenant_id=:tenant AND id=:document
        """),
            {"tenant": tenant_id, "document": document_id},
        )

    @staticmethod
    def _job(row: RowMapping) -> IngestionJob:
        values = dict(row)
        values["state"] = JobState(values["state"])
        if values["resume_from"] is not None:
            values["resume_from"] = JobState(values["resume_from"])
        return IngestionJob(**values)
