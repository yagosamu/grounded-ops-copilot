"""Witness a disposable local restore; never target a production database."""

import json
import re
import subprocess
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from mypy_boto3_s3 import S3Client
from opensearchpy import OpenSearch
from sqlalchemy import Engine, create_engine, inspect, text

from adapters.object_store.document_store import ArtifactRef, DocumentStore
from adapters.opensearch.index_schema import LexicalIndexSchema
from modules.indexing.rebuild import IndexRebuilder
from modules.indexing.replay import ReplaySummary, replay_authoritative


def run_local_recovery(
    source: Engine,
    source_store: DocumentStore,
    s3_client: S3Client,
    search: OpenSearch,
    report_path: Path,
    *,
    chunk_max_tokens: int,
) -> dict[str, Any]:
    """Back up an isolated Compose schema and restore it to fresh destinations."""
    if (
        source.url.host != "127.0.0.1"
        or urlparse(s3_client.meta.endpoint_url).hostname != "127.0.0.1"
        or source_store.client is not s3_client
        or not search.transport.hosts
        or any(host.get("host") != "127.0.0.1" for host in search.transport.hosts)
    ):
        raise ValueError("local recovery requires local Compose dependencies")
    schema = _schema_name(source)
    container = _postgres_container(source)
    database_name = source.url.database
    username = source.url.username
    if database_name != "ingestion_test" or username != "ingestion_test":
        raise ValueError("local recovery only accepts the ephemeral test database")

    dump = _docker(
        container,
        "pg_dump",
        "-U",
        username,
        "-d",
        database_name,
        "--schema",
        schema,
        "--format=custom",
    )
    source_checksums = _table_checksums(source, schema)
    backup_bucket = f"recovery-backup-{uuid4().hex}"
    restore_bucket = f"recovery-restored-{uuid4().hex}"
    backup_store = _new_store(s3_client, backup_bucket)
    backup_keys = _copy_verified(source_store, backup_store, s3_client)
    restore_database = f"recovery_{uuid4().hex}"
    index_prefix = f"recovery-{uuid4().hex}"
    administration = create_engine(source.url)
    target: Engine | None = None
    created_database = False
    started = perf_counter()
    try:
        with administration.connect().execution_options(
            isolation_level="AUTOCOMMIT"
        ) as connection:
            connection.exec_driver_sql(f'CREATE DATABASE "{restore_database}"')
        created_database = True
        restored_url = source.url.set(database=restore_database)
        _docker(
            container,
            "pg_restore",
            "-U",
            username,
            "-d",
            restore_database,
            "--no-owner",
            "--no-privileges",
            stdin=dump,
        )
        target = create_engine(
            restored_url,
            connect_args={"options": f"-csearch_path={schema}", "connect_timeout": 3},
        )
        target_checksums = _table_checksums(target, schema)
        if target_checksums != source_checksums:
            raise RuntimeError("restored metadata checksum mismatch")

        restored_store = _new_store(s3_client, restore_bucket)
        restored_keys = _copy_verified(backup_store, restored_store, s3_client)
        if restored_keys != backup_keys:
            raise RuntimeError("restored artifact checksum mismatch")

        catalog = LexicalIndexSchema(search, index_prefix, replicas=0)
        catalog.ensure("1")
        replay: ReplaySummary | None = None

        def build(candidate: str) -> None:
            nonlocal replay
            replay = replay_authoritative(
                target,
                restored_store,
                search,
                candidate,
                chunk_max_tokens=chunk_max_tokens,
            )

        def validate(candidate: str) -> bool:
            if replay is None or replay.chunks > 10_000:
                return False
            hits = _hits(search, candidate, replay.chunks)
            return {hit["_id"] for hit in hits} == set(replay.chunk_ids)

        IndexRebuilder(catalog).rebuild("2", build=build, validate=validate)
        if replay is None:
            raise RuntimeError("index replay did not run")
        evidence = _hits(search, catalog.read_alias, replay.chunks)
        rto_seconds = round(perf_counter() - started, 3)
        report: dict[str, Any] = {
            "result": "pass",
            "environment": "local-compose",
            "witnessed_at_utc": datetime.now(UTC).isoformat(),
            "database_backup_sha256": sha256(dump).hexdigest(),
            "metadata_table_sha256": target_checksums,
            "metadata_checksums_match": True,
            "artifact_sha256": backup_keys,
            "artifact_checksums_match": True,
            "restored_documents": replay.documents,
            "restored_versions": replay.versions,
            "restored_chunks": replay.chunks,
            "search_evidence": [
                {
                    "tenant_id": hit["_source"]["tenant_id"],
                    "document_version_id": hit["_source"]["document_version_id"],
                    "policy": hit["_source"]["policy"],
                    "text_sha256": sha256(hit["_source"]["text"].encode()).hexdigest(),
                }
                for hit in evidence
            ],
            "rpo_seconds": 0,
            "rpo_target_seconds": 86_400,
            "rto_seconds": rto_seconds,
            "rto_target_seconds": 14_400,
            "limitations": [
                "Writes were paused; zero RPO is observed data loss in this "
                "exercise, not a backup-frequency guarantee.",
                "Local Compose restore does not validate AWS RDS snapshots "
                "or S3 disaster recovery.",
            ],
        }
        if rto_seconds > 14_400:
            raise RuntimeError("local recovery exceeded RTO target")
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return report
    finally:
        if target is not None:
            target.dispose()
        if created_database:
            with administration.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as connection:
                connection.exec_driver_sql(
                    f'DROP DATABASE "{restore_database}" WITH (FORCE)'
                )
        administration.dispose()
        search.indices.delete(index=f"{index_prefix}-*", ignore_unavailable=True)


def _schema_name(engine: Engine) -> str:
    with engine.connect() as connection:
        schema = str(connection.execute(text("SELECT current_schema()")).scalar_one())
    if re.fullmatch(r"test_[a-f0-9]{32}", schema) is None:
        raise ValueError("local recovery only accepts an ephemeral test schema")
    return schema


def _postgres_container(engine: Engine) -> str:
    port = engine.url.port
    if port is None:
        raise ValueError("local recovery requires a mapped test port")
    result = subprocess.run(
        ["docker", "ps", "--format", "{{json .}}"],
        check=True,
        capture_output=True,
        text=True,
    )
    containers = [
        entry["ID"]
        for line in result.stdout.splitlines()
        if f":{port}->5432/tcp" in (entry := json.loads(line))["Ports"]
        and entry["Names"].startswith("grounded-test-")
        and entry["Image"] == "postgres:16.10-alpine"
    ]
    if len(containers) != 1:
        raise RuntimeError("ephemeral PostgreSQL container is not unique")
    return containers[0]


def _docker(container: str, *command: str, stdin: bytes | None = None) -> bytes:
    result = subprocess.run(
        ["docker", "exec", "-i", container, *command],
        input=stdin,
        capture_output=True,
        check=True,
    )
    return result.stdout


def _table_checksums(engine: Engine, schema: str) -> dict[str, str]:
    with engine.connect() as connection:
        tables = inspect(connection).get_table_names(schema=schema)
        checksums: dict[str, str] = {}
        for table in tables:
            rows = connection.exec_driver_sql(
                f'SELECT * FROM "{schema}"."{table}"'
            ).mappings()
            encoded = sorted(
                json.dumps(dict(row), sort_keys=True, default=str) for row in rows
            )
            checksums[table] = sha256("\n".join(encoded).encode()).hexdigest()
        return checksums


def _new_store(client: S3Client, bucket: str) -> DocumentStore:
    client.create_bucket(Bucket=bucket)
    return DocumentStore(client, bucket)


def _copy_verified(
    source: DocumentStore, target: DocumentStore, client: S3Client
) -> dict[str, str]:
    copied: dict[str, str] = {}
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=source.bucket):
        for item in page.get("Contents", []):
            key = str(item["Key"])
            parts = key.split("/")
            if len(parts) != 3:
                raise RuntimeError("invalid backup artifact key")
            reference = ArtifactRef(*parts)
            content = source.get(reference.tenant_id, reference)
            if target.put(reference.tenant_id, reference.kind, content) != reference:
                raise RuntimeError("copied artifact checksum mismatch")
            copied[key] = reference.digest
    return copied


def _hits(client: OpenSearch, index: str, count: int) -> list[dict[str, Any]]:
    response = client.search(
        index=index,
        body={"query": {"match_all": {}}, "size": max(count, 1)},
    )
    hits: list[dict[str, Any]] = response["hits"]["hits"]
    if len(hits) != count:
        raise RuntimeError("restored search count mismatch")
    return hits
