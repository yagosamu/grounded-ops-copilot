"""AWS Pilot worker wiring keeps cloud credentials out of Terraform and env."""

from types import SimpleNamespace
from typing import Any

import pytest

from grounded_ops import worker

pytestmark = pytest.mark.unit


def test_worker_uses_task_identity_for_s3_and_signed_opensearch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created: dict[str, Any] = {}

    class Resource:
        meta = SimpleNamespace(
            config=SimpleNamespace(
                connect_timeout=2,
                read_timeout=5,
                retries={"total_max_attempts": 1},
            )
        )

        def close(self) -> None:
            pass

        def dispose(self) -> None:
            pass

    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only-password")
    monkeypatch.setenv("POSTGRES_HOST", "database.internal")
    monkeypatch.setenv("S3_BUCKET", "pilot-artifacts")
    monkeypatch.setenv("OPENSEARCH_URL", "https://search.internal")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    monkeypatch.delenv("MINIO_ROOT_USER", raising=False)
    monkeypatch.delenv("MINIO_ROOT_PASSWORD", raising=False)

    def create_s3(service: str, **options: object) -> Resource:
        created["s3"] = (service, options)
        return Resource()

    def create_search(**options: object) -> Resource:
        created["search"] = options
        return Resource()

    monkeypatch.setattr(worker, "create_engine", lambda *args, **kwargs: Resource())
    monkeypatch.setattr(worker.boto3, "client", create_s3)
    monkeypatch.setattr(worker, "OpenSearch", create_search)
    monkeypatch.setattr(
        worker.boto3,
        "Session",
        lambda: SimpleNamespace(get_credentials=lambda: "task-credentials"),
    )
    monkeypatch.setattr(
        worker,
        "AWSV4SignerAuth",
        lambda credentials, region, service: (credentials, region, service),
    )

    runtime = worker._build_runtime()
    assert created["s3"][0] == "s3"
    assert "aws_access_key_id" not in created["s3"][1]
    assert "aws_secret_access_key" not in created["s3"][1]
    assert created["s3"][1]["region_name"] == "us-east-1"
    assert created["search"]["hosts"] == ["https://search.internal"]
    assert created["search"]["http_auth"] == (
        "task-credentials",
        "us-east-1",
        "es",
    )
    assert created["search"]["use_ssl"] is True
    assert created["search"]["verify_certs"] is True
    assert runtime.close is not None
    runtime.close()


def test_worker_rejects_missing_task_identity_before_opening_clients(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[str] = []

    class Engine:
        def dispose(self) -> None:
            opened.append("database_closed")

    monkeypatch.setenv("POSTGRES_PASSWORD", "test-only-password")
    monkeypatch.setenv("S3_BUCKET", "pilot-artifacts")
    monkeypatch.setenv("OPENSEARCH_URL", "https://search.internal")
    monkeypatch.delenv("S3_ENDPOINT", raising=False)
    monkeypatch.setattr(worker, "create_engine", lambda *args, **kwargs: Engine())
    monkeypatch.setattr(
        worker.boto3,
        "Session",
        lambda: SimpleNamespace(get_credentials=lambda: None),
    )
    monkeypatch.setattr(
        worker.boto3,
        "client",
        lambda *args, **kwargs: opened.append("s3_opened"),
    )
    monkeypatch.setattr(
        worker,
        "OpenSearch",
        lambda **options: opened.append("search_opened"),
    )

    with pytest.raises(RuntimeError, match="AWS task credentials are unavailable"):
        worker._build_runtime()
    assert opened == ["database_closed"]
