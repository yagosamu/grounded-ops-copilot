"""ING-02 immutable artifact storage at the S3 adapter boundary."""

import pytest
from mypy_boto3_s3 import S3Client

from adapters.object_store.document_store import (
    ArtifactError,
    ArtifactRef,
    DocumentStore,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("kind", ["raw", "normalized"])
def test_identical_content_reuses_verified_tenant_object(
    s3_client: S3Client, artifact_bucket: str, kind: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    original = store.put("alpha", kind, b"abc")
    assert (
        original.digest
        == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )
    assert original.key == f"alpha/{kind}/{original.digest}"
    assert store.put("alpha", kind, b"abc") == original
    assert store.get("alpha", original) == b"abc"


def test_corruption_is_rejected_on_read_and_cannot_be_overwritten(
    s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    reference = store.put("alpha", "raw", b"original")
    s3_client.put_object(Bucket=artifact_bucket, Key=reference.key, Body=b"corrupt")
    with pytest.raises(ArtifactError, match="artifact integrity failed"):
        store.get("alpha", reference)
    with pytest.raises(ArtifactError, match="artifact integrity failed"):
        store.put("alpha", "raw", b"original")


def test_missing_objects_and_cross_tenant_reads_fail_closed(
    s3_client: S3Client, artifact_bucket: str
) -> None:
    store = DocumentStore(s3_client, artifact_bucket)
    alpha = store.put("alpha", "normalized", b"abc")
    beta = store.put("beta", "normalized", b"abc")
    assert alpha.key != beta.key
    assert store.get("beta", beta) == b"abc"
    with pytest.raises(ArtifactError, match="artifact unavailable"):
        store.get("beta", alpha)
    missing = ArtifactRef("alpha", "raw", "0" * 64)
    with pytest.raises(ArtifactError, match="artifact unavailable"):
        store.get("alpha", missing)


def test_dependency_failure_is_redacted(s3_client: S3Client) -> None:
    with pytest.raises(ArtifactError) as error:
        DocumentStore(s3_client, "missing-bucket").put("alpha", "raw", b"private")
    assert str(error.value) == "artifact unavailable"
    assert "private" not in str(error.value)


@pytest.mark.parametrize(
    "tenant,kind,digest",
    [
        ("../other", "raw", "a" * 64),
        ("alpha", "../raw", "a" * 64),
        ("alpha", "raw", "../hash"),
    ],
)
def test_forged_references_cannot_escape_prefix(
    tenant: str, kind: str, digest: str
) -> None:
    with pytest.raises(ValueError):
        ArtifactRef(tenant, kind, digest)


def test_uploads_have_a_hard_size_bound(
    s3_client: S3Client, artifact_bucket: str
) -> None:
    with pytest.raises(ArtifactError, match="size limit"):
        DocumentStore(s3_client, artifact_bucket).put(
            "alpha", "raw", b"x" * (10 * 1024 * 1024 + 1)
        )
