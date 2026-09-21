"""Integration proof for correlation across HTTP and worker boundaries."""

from __future__ import annotations

import json
import logging

import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from grounded_ops.app import create_app
from observability.telemetry import (
    EntryPoint,
    Telemetry,
    TelemetryComponent,
    TelemetryOperation,
)

pytestmark = pytest.mark.integration


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def telemetry_signals() -> tuple[Telemetry, ListHandler, InMemorySpanExporter]:
    handler = ListHandler()
    logger = logging.Logger("telemetry-integration-test")
    logger.addHandler(handler)
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    meter_provider = MeterProvider(metric_readers=[InMemoryMetricReader()])
    telemetry = Telemetry(
        logger=logger,
        tracer=tracer_provider.get_tracer("telemetry-integration-test"),
        meter=meter_provider.get_meter("telemetry-integration-test"),
    )
    return telemetry, handler, span_exporter


def test_trace_and_correlation_propagate_from_api_to_all_runtime_components() -> None:
    telemetry, handler, span_exporter = telemetry_signals()
    correlation_id = "5a58613e-7ae5-4cc8-a7ea-d83ea3fb738c"
    api_context = telemetry.new_context(
        correlation_id,
        entry_point=EntryPoint.API,
    )

    with telemetry.bind(api_context):
        with telemetry.operation(
            TelemetryComponent.API,
            TelemetryOperation.HTTP_REQUEST,
        ):
            carrier = telemetry.inject_carrier()

    with telemetry.bind_carrier(carrier):
        for component, operation in (
            (TelemetryComponent.WORKER, TelemetryOperation.INGESTION_RUN),
            (TelemetryComponent.RETRIEVAL, TelemetryOperation.RETRIEVAL_QUERY),
            (TelemetryComponent.GENERATION, TelemetryOperation.GENERATION_REQUEST),
            (TelemetryComponent.AGENT, TelemetryOperation.INVESTIGATION_RUN),
        ):
            with telemetry.operation(component, operation):
                pass

    spans = span_exporter.get_finished_spans()
    payloads = [json.loads(message) for message in handler.messages]
    api_span = next(span for span in spans if span.name == "http.request")
    downstream_spans = [span for span in spans if span.name != "http.request"]

    assert "traceparent" in carrier
    assert len({span.context.trace_id for span in spans}) == 1
    assert all(
        span.parent is not None and span.parent.span_id == api_span.context.span_id
        for span in downstream_spans
    )
    assert {payload["component"] for payload in payloads} == {
        "api",
        "worker",
        "retrieval",
        "generation",
        "agent",
    }
    assert {payload["correlation_id"] for payload in payloads} == {
        "5a58613e7ae54cc8a7ead83ea3fb738c"
    }
    assert {payload["entry_point"] for payload in payloads} == {"api"}


def test_http_middleware_replaces_unsafe_correlation_header() -> None:
    telemetry, handler, _ = telemetry_signals()
    unsafe_header = "authorization-token-must-not-leak"

    response = TestClient(create_app(telemetry=telemetry)).get(
        "/health/live",
        headers={"X-Correlation-ID": unsafe_header},
    )

    assert response.status_code == 200
    assert response.headers["X-Correlation-ID"] != unsafe_header
    assert len(response.headers["X-Correlation-ID"]) == 32
    assert unsafe_header not in repr(handler.messages)
    assert json.loads(handler.messages[0])["component"] == "api"


def test_http_middleware_classifies_server_errors() -> None:
    telemetry, handler, _ = telemetry_signals()
    app = create_app(telemetry=telemetry)

    @app.get("/unavailable", status_code=503)
    def unavailable() -> dict[str, str]:
        return {"status": "unavailable"}

    response = TestClient(app).get("/unavailable")

    assert response.status_code == 503
    assert json.loads(handler.messages[0])["outcome"] == "error"
