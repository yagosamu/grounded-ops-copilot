"""Executable worker commands and bounded task dispatch."""

import sys
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from sqlalchemy import URL

from adapters.sources.markdown import SourceEvent
from grounded_ops import worker
from observability.telemetry import EntryPoint, Telemetry

pytestmark = pytest.mark.unit


def test_worker_runtime_wires_resources_and_closes_them(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[str] = []

    class FakeEngine:
        def dispose(self) -> None:
            closed.append("database")

    class FakeS3:
        meta = SimpleNamespace(
            config=SimpleNamespace(
                connect_timeout=2,
                read_timeout=5,
                retries={"total_max_attempts": 1},
            )
        )

        def close(self) -> None:
            closed.append("object_store")

    class FakeSearch:
        def close(self) -> None:
            closed.append("search")

    created: dict[str, Any] = {}

    def create_engine(url: object, **options: object) -> FakeEngine:
        assert isinstance(url, URL)
        created["database_host"] = url.host
        created["database_name"] = url.database
        created["options"] = options
        return FakeEngine()

    def create_s3(service: str, **options: object) -> FakeS3:
        created["s3_service"] = service
        created["s3_endpoint"] = options["endpoint_url"]
        return FakeS3()

    def create_search(**options: object) -> FakeSearch:
        created["search_hosts"] = options["hosts"]
        return FakeSearch()

    for key, value in {
        "POSTGRES_PASSWORD": "local-secret",
        "MINIO_ROOT_USER": "local-user",
        "MINIO_ROOT_PASSWORD": "local-secret",
        "S3_ENDPOINT": "http://minio:9000",
        "S3_BUCKET": "artifacts",
        "OPENSEARCH_URL": "http://opensearch:9200",
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(worker, "create_engine", create_engine)
    monkeypatch.setattr(worker.boto3, "client", create_s3)
    monkeypatch.setattr(worker, "OpenSearch", create_search)

    runtime = worker._build_runtime()
    assert created["database_host"] == "postgres"
    assert created["database_name"] == "grounded_ops"
    assert created["options"] == {"pool_pre_ping": True}
    assert created["s3_service"] == "s3"
    assert created["s3_endpoint"] == "http://minio:9000"
    assert created["search_hosts"] == ["http://opensearch:9200"]
    assert isinstance(
        runtime.sink_factory(SimpleNamespace()), worker.OpenSearchPreparationSink
    )
    assert runtime.close is not None
    runtime.close()
    assert closed == ["search", "object_store", "database"]


def test_celery_task_schedules_checkpoint_retry_and_closes_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scheduled: list[tuple[tuple[str, str], int]] = []
    closed: list[bool] = []
    contexts: list[tuple[str, EntryPoint]] = []
    job_id = "0123456789abcdef0123456789abcdef"
    monkeypatch.setattr(
        worker,
        "_build_runtime",
        lambda: worker.WorkerRuntime(
            SimpleNamespace(),
            SimpleNamespace(),
            lambda _repository: SimpleNamespace(),
            lambda: closed.append(True),
        ),
    )

    def run_job(*args: object) -> str:
        context = Telemetry().current_context()
        assert context is not None
        contexts.append((context.correlation_id, context.entry_point))
        return "retrying"

    monkeypatch.setattr(worker, "run_job", run_job)
    monkeypatch.setattr(
        worker.process_ingestion,
        "apply_async",
        lambda *, args, countdown: scheduled.append((args, countdown)),
    )

    assert worker.process_ingestion.run("alpha", job_id) == "retrying"
    assert contexts == [(job_id, EntryPoint.WORKER)]
    assert scheduled == [(("alpha", job_id), 5)]
    assert closed == [True]


def test_recovery_task_closes_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[bool] = []
    monkeypatch.setattr(
        worker,
        "_build_runtime",
        lambda: worker.WorkerRuntime(
            SimpleNamespace(),
            SimpleNamespace(),
            lambda _repository: SimpleNamespace(),
            lambda: closed.append(True),
        ),
    )
    monkeypatch.setattr(worker, "recover_pending", lambda engine, queue: 2)

    assert worker.recover_ingestion.run() == 2
    assert closed == [True]


def test_cli_submits_corpus_and_closes_runtime(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    submitted: list[tuple[str, tuple[str, ...], str]] = []
    closed: list[bool] = []
    monkeypatch.setattr(
        worker,
        "_build_runtime",
        lambda: worker.WorkerRuntime(
            SimpleNamespace(),
            SimpleNamespace(),
            lambda _repository: SimpleNamespace(),
            lambda: closed.append(True),
        ),
    )

    def submit(event: object, engine: object, store: object, queue: object) -> object:
        source_event = cast(SourceEvent, event)
        source = source_event.source
        submitted.append((source.tenant_id, source.policy, source_event.kind))
        return SimpleNamespace(id=f"job-{len(submitted)}")

    monkeypatch.setattr(worker, "submit_event", submit)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "worker",
            "submit-corpus",
            "evals/datasets/v1",
            "--tenant",
            "alpha",
            "--policy",
            "engineers",
        ],
    )
    worker.main()

    assert submitted
    assert all(item == ("alpha", ("engineers",), "new") for item in submitted)
    assert capsys.readouterr().out.splitlines() == [
        f"job-{index}" for index in range(1, len(submitted) + 1)
    ]
    assert closed == [True]


def test_cli_migrates_and_recovers(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    calls: list[str] = []

    class FakeEngine:
        @contextmanager
        def begin(self):
            yield "connection"

    monkeypatch.setattr(
        worker,
        "_build_runtime",
        lambda: worker.WorkerRuntime(
            FakeEngine(),
            SimpleNamespace(),
            lambda _repository: SimpleNamespace(),
            lambda: calls.append("closed"),
        ),
    )
    monkeypatch.setattr(
        worker, "migrate", lambda connection, revision: calls.append(revision)
    )
    monkeypatch.setattr(worker, "recover_pending", lambda engine, queue: 2)

    monkeypatch.setattr(sys, "argv", ["worker", "migrate"])
    worker.main()
    monkeypatch.setattr(sys, "argv", ["worker", "recover"])
    worker.main()

    assert calls == ["head", "closed", "closed"]
    assert capsys.readouterr().out == "2\n"
