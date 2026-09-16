"""ING-01/02 repository behavior against real PostgreSQL transactions."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier

import pytest
from sqlalchemy import Engine
from sqlalchemy.exc import ProgrammingError

from adapters.postgres.ingestion_repository import IngestionRepository
from adapters.postgres.migrations import migrate
from domain.ingestion import JobState, Source

pytestmark = pytest.mark.integration
SOURCE = Source("otel", "alpha", "markdown", "docs/trace.md", ("engineers",))
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def test_submit_round_trips_tenant_source_version_and_job(database: Engine) -> None:
    with database.begin() as connection:
        repository = IngestionRepository(connection)
        submitted = repository.submit(
            SOURCE, "trace.md", "rev1", "a" * 64, STAMP, "markdown-v1"
        )
    with database.begin() as connection:
        repository = IngestionRepository(connection)
        assert repository.get_source("alpha", "otel") == SOURCE
        assert (
            repository.get_document("alpha", submitted.document.id)
            == submitted.document
        )
        assert (
            repository.get_version("alpha", submitted.version.id) == submitted.version
        )
        assert repository.get_job("alpha", submitted.job.id) == submitted.job
        assert submitted.job.state == JobState.QUEUED
        assert submitted.version.source_timestamp == STAMP
        assert submitted.version.content_hash == "a" * 64
        assert submitted.document.current_version_id is None
        assert repository.get_version("beta", submitted.version.id) is None
        assert repository.get_job("beta", submitted.job.id) is None
        assert repository.get_document("beta", submitted.document.id) is None
        assert repository.get_source("beta", "otel") is None


def test_duplicate_and_update_retain_history_and_guard_current_order(
    database: Engine,
) -> None:
    with database.begin() as connection:
        repository = IngestionRepository(connection)
        old = repository.submit(SOURCE, "trace.md", "rev1", "a" * 64, STAMP, "p1")
        duplicate = repository.submit(SOURCE, "trace.md", "rev1", "a" * 64, STAMP, "p1")
        assert duplicate == old
        newer = repository.submit(
            SOURCE, "trace.md", "rev2", "b" * 64, datetime(2026, 2, 1, tzinfo=UTC), "p1"
        )
        repository.promote("alpha", newer.version.id)
        repository.promote("alpha", old.version.id)
        current = repository.get_document("alpha", old.document.id)
        assert current.current_version_id == newer.version.id
        assert repository.list_versions("alpha", old.document.id) == [
            old.version,
            newer.version,
        ]
        assert repository.list_versions("beta", old.document.id) == []
        repository.delete("beta", old.document.id)
        assert repository.get_document("alpha", old.document.id) == current
        repository.delete("alpha", old.document.id)
        repository.delete("alpha", old.document.id)
        assert repository.get_document("alpha", old.document.id) == current.delete()
        repository.promote("alpha", newer.version.id)
        assert repository.get_document("alpha", old.document.id) == current.delete()
        with pytest.raises(ValueError, match="unknown document version"):
            repository.promote("beta", newer.version.id)


def test_concurrent_submission_has_one_canonical_job_and_version(
    database: Engine,
) -> None:
    barrier = Barrier(4)

    def submit(_: int):
        barrier.wait(timeout=10)
        with database.begin() as connection:
            return IngestionRepository(connection).submit(
                SOURCE, "trace.md", "rev1", "a" * 64, STAMP, "p1"
            )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(submit, range(4)))
    assert results == [results[0]] * 4
    with database.begin() as connection:
        assert IngestionRepository(connection).list_versions(
            "alpha", results[0].document.id
        ) == [results[0].version]


def test_transaction_rollback_leaves_no_partial_source(database: Engine) -> None:
    with pytest.raises(RuntimeError, match="abort"):
        with database.begin() as connection:
            IngestionRepository(connection).submit(
                SOURCE, "trace.md", "rev1", "a" * 64, STAMP, "p1"
            )
            raise RuntimeError("abort")
    with database.begin() as connection:
        assert IngestionRepository(connection).get_source("alpha", "otel") is None


def test_same_source_version_cannot_rewrite_content(database: Engine) -> None:
    with database.begin() as connection:
        original = IngestionRepository(connection).submit(
            SOURCE, "trace.md", "rev1", "a" * 64, STAMP, "p1"
        )
    with pytest.raises(ValueError, match="source version content conflict"):
        with database.begin() as connection:
            IngestionRepository(connection).submit(
                SOURCE, "trace.md", "rev1", "b" * 64, STAMP, "p1"
            )
    with database.begin() as connection:
        assert (
            IngestionRepository(connection).get_version("alpha", original.version.id)
            == original.version
        )


def test_migrations_down_and_up_restore_a_usable_repository(database: Engine) -> None:
    with database.begin() as connection:
        migrate(connection, "base")
    with pytest.raises(ProgrammingError):
        with database.begin() as connection:
            IngestionRepository(connection).get_source("alpha", "otel")
    with database.begin() as connection:
        migrate(connection, "head")
        result = IngestionRepository(connection).submit(
            SOURCE, "trace.md", "rev1", "a" * 64, STAMP, "p1"
        )
        assert result.job.state == JobState.QUEUED
