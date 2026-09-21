"""Unit tests for the content-free telemetry contract."""

from __future__ import annotations

import json
import logging

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)

from observability.telemetry import (
    EntryPoint,
    Telemetry,
    TelemetryComponent,
    TelemetryOperation,
)

pytestmark = pytest.mark.unit


class ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.messages.append(record.getMessage())


def telemetry_signals() -> tuple[
    Telemetry,
    ListHandler,
    InMemorySpanExporter,
    InMemoryMetricReader,
]:
    handler = ListHandler()
    logger = logging.Logger("telemetry-test")
    logger.addHandler(handler)
    span_exporter = InMemorySpanExporter()
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(SimpleSpanProcessor(span_exporter))
    metric_reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[metric_reader])
    telemetry = Telemetry(
        logger=logger,
        tracer=tracer_provider.get_tracer("telemetry-test"),
        meter=meter_provider.get_meter("telemetry-test"),
    )
    return telemetry, handler, span_exporter, metric_reader


def test_operation_emits_allowlisted_signals_without_sensitive_content() -> None:
    telemetry, handler, span_exporter, metric_reader = telemetry_signals()
    corpus = "private incident details"
    secret = "sk-production-secret"
    context = telemetry.new_context(
        "3a497821-ca0c-42cd-a339-86f444ca1240",
        entry_point=EntryPoint.API,
    )

    with telemetry.bind(context):
        with pytest.raises(RuntimeError, match="private incident"):
            with telemetry.operation(
                TelemetryComponent.GENERATION,
                TelemetryOperation.GENERATION_REQUEST,
            ):
                raise RuntimeError(f"{corpus}: {secret}")

    log_payload = json.loads(handler.messages[0])
    span = span_exporter.get_finished_spans()[0]
    metric_data = metric_reader.get_metrics_data()
    signals = f"{handler.messages!r} {span.attributes!r} {metric_data!r}"
    metrics = {
        metric.name: metric
        for resource_metrics in metric_data.resource_metrics
        for scope_metrics in resource_metrics.scope_metrics
        for metric in scope_metrics.metrics
    }
    metric_attributes = {
        tuple(sorted(point.attributes.items()))
        for metric in metrics.values()
        for point in metric.data.data_points
    }

    assert log_payload == {
        "component": "generation",
        "correlation_id": "3a497821ca0c42cda33986f444ca1240",
        "duration_ms": log_payload["duration_ms"],
        "entry_point": "api",
        "error_type": "RuntimeError",
        "event": "operation_failed",
        "operation": "generation.request",
        "outcome": "error",
    }
    assert set(metrics) == {
        "grounded_ops_operation_duration_seconds",
        "grounded_ops_operations_total",
    }
    assert metric_attributes == {
        (
            ("component", "generation"),
            ("operation", "generation.request"),
            ("outcome", "error"),
        )
    }
    assert span.events == ()
    assert corpus not in signals
    assert secret not in signals


def test_external_correlation_and_entry_point_are_normalized() -> None:
    telemetry, _, _, _ = telemetry_signals()
    unsafe_value = "customer@example.com sk-secret"

    with telemetry.bind_carrier(
        {
            "x-correlation-id": unsafe_value,
            "x-entry-point": unsafe_value,
        }
    ) as context:
        carrier = telemetry.inject_carrier()

    assert context.correlation_id != unsafe_value
    assert len(context.correlation_id) == 32
    assert context.entry_point is EntryPoint.WORKER
    assert carrier["x-entry-point"] == "worker"
    assert unsafe_value not in repr(carrier)


def test_carrier_requires_an_explicit_correlation_context() -> None:
    telemetry, _, _, _ = telemetry_signals()

    with pytest.raises(RuntimeError, match="telemetry context is not bound"):
        telemetry.inject_carrier()
