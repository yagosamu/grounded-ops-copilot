"""ING-01: callers observe guarded ingestion lifecycle transitions."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime

import pytest

from domain.ingestion import (
    Document,
    DocumentVersion,
    IngestionJob,
    InvalidTransition,
    JobState,
    Source,
)

pytestmark = pytest.mark.unit

ALLOWED = {
    ("queued", "fetching"),
    ("fetching", "parsing"),
    ("parsing", "indexing"),
    ("indexing", "completed"),
    ("fetching", "retrying"),
    ("parsing", "retrying"),
    ("indexing", "retrying"),
    ("retrying", "fetching"),
    ("retrying", "parsing"),
    ("retrying", "indexing"),
    ("retrying", "failed"),
}


@pytest.mark.parametrize("before", JobState)
@pytest.mark.parametrize("after", JobState)
def test_all_job_transitions_are_guarded(before: JobState, after: JobState) -> None:
    job = IngestionJob(
        id="job", tenant_id="tenant", idempotency_key="key", state=before
    )
    if (before.value, after.value) in ALLOWED:
        changed = job.transition(after)
        assert changed.state == after
        assert job.state == before
        assert (changed.id, changed.tenant_id, changed.idempotency_key) == (
            "job",
            "tenant",
            "key",
        )
    else:
        with pytest.raises(InvalidTransition) as error:
            job.transition(after)
        assert error.value.event == {
            "event": "invalid_ingestion_transition",
            "job_id": "job",
            "tenant_id": "tenant",
            "from": before.value,
            "to": after.value,
        }


def test_versioned_corpus_preserves_identity_policy_and_provenance() -> None:
    source = Source("otel", "tenant", "markdown", "docs/trace.md", ("engineers",))
    document = Document("doc", "tenant", "otel", "trace.md")
    version = DocumentVersion(
        "v1",
        "tenant",
        "doc",
        "rev1",
        "a" * 64,
        datetime(2026, 1, 1, tzinfo=UTC),
        "markdown-v1",
        "tenant/raw/hash",
    )
    assert (source.id, source.tenant_id, source.policy, source.external_ref) == (
        "otel",
        "tenant",
        ("engineers",),
        "docs/trace.md",
    )
    assert (document.source_id, document.canonical_key, document.deleted) == (
        "otel",
        "trace.md",
        False,
    )
    assert (version.document_id, version.source_version, version.content_hash) == (
        "doc",
        "rev1",
        "a" * 64,
    )
    assert (version.source_timestamp, version.parser_version, version.raw_ref) == (
        datetime(2026, 1, 1, tzinfo=UTC),
        "markdown-v1",
        "tenant/raw/hash",
    )
    with pytest.raises(FrozenInstanceError):
        source.tenant_id = "other"


@pytest.mark.parametrize("identifier", ["", "x" * 129, "../tenant", "bad\nvalue"])
def test_external_identifiers_have_explicit_bounds(identifier: str) -> None:
    with pytest.raises(ValueError, match="identifier"):
        Source("source", identifier, "markdown", "docs/trace.md", ("read",))


def test_source_requires_nonempty_access_policy() -> None:
    with pytest.raises(ValueError, match="policy"):
        Source("source", "tenant", "markdown", "docs/trace.md", ())


@pytest.mark.parametrize("deleted", [False, True])
def test_document_deletion_is_idempotent_and_clears_current_version(
    deleted: bool,
) -> None:
    document = Document("doc", "tenant", "source", "key", "v1", deleted)
    tombstone = document.delete()
    assert tombstone.deleted is True
    assert tombstone.current_version_id is None
    assert tombstone.delete() == tombstone


@pytest.mark.parametrize("digest", ["", "g" * 64, "a" * 63])
def test_document_version_requires_sha256_provenance(digest: str) -> None:
    with pytest.raises(ValueError, match="hash"):
        DocumentVersion("v", "t", "d", "rev", digest, datetime.now(UTC), "p1")


def test_source_timestamp_requires_timezone() -> None:
    with pytest.raises(ValueError, match="timezone"):
        DocumentVersion("v", "t", "d", "rev", "a" * 64, datetime(2026, 1, 1), "p1")


@pytest.mark.parametrize("reference", ["", "x" * 2049])
def test_external_references_and_canonical_keys_are_bounded(reference: str) -> None:
    with pytest.raises(ValueError, match="reference"):
        Source("s", "t", "markdown", reference, ("read",))
    with pytest.raises(ValueError, match="canonical"):
        Document("d", "t", "s", reference)


def test_jobs_reject_negative_attempts() -> None:
    with pytest.raises(ValueError, match="attempts"):
        IngestionJob("j", "t", "key", attempts=-1)
