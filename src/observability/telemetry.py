"""Correlated, allowlisted logs, metrics and OpenTelemetry spans."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from typing import Protocol
from uuid import UUID, uuid4

from opentelemetry import context as otel_context
from opentelemetry import metrics, propagate, trace
from opentelemetry.metrics import Counter, Histogram, Meter
from opentelemetry.trace import Span, Status, StatusCode, Tracer
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp


class TelemetryComponent(StrEnum):
    API = "api"
    WORKER = "worker"
    RETRIEVAL = "retrieval"
    GENERATION = "generation"
    AGENT = "agent"


class TelemetryOperation(StrEnum):
    HTTP_REQUEST = "http.request"
    INGESTION_RUN = "ingestion.run"
    INGESTION_RESUME = "ingestion.resume"
    RETRIEVAL_QUERY = "retrieval.query"
    GENERATION_REQUEST = "generation.request"
    INVESTIGATION_RUN = "investigation.run"
    INVESTIGATION_STEP = "investigation.step"


class TelemetryOutcome(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    PARTIAL = "partial"
    CANCELLED = "cancelled"


class EntryPoint(StrEnum):
    API = "api"
    WORKER = "worker"
    CLI = "cli"
    INTERNAL = "internal"


@dataclass(frozen=True)
class TelemetryContext:
    correlation_id: str
    entry_point: EntryPoint


@dataclass
class Observation:
    outcome: TelemetryOutcome = TelemetryOutcome.SUCCESS


class Clock(Protocol):
    def __call__(self) -> float: ...


class _NoOpCounter:
    def add(
        self,
        amount: int | float,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        return


class _NoOpHistogram:
    def record(
        self,
        amount: int | float,
        attributes: Mapping[str, object] | None = None,
    ) -> None:
        return


_CURRENT: ContextVar[TelemetryContext | None] = ContextVar(
    "grounded_ops_telemetry_context", default=None
)


class Telemetry:
    """Emit only bounded operational metadata and propagate W3C trace context."""

    def __init__(
        self,
        *,
        logger: logging.Logger | None = None,
        tracer: Tracer | None = None,
        meter: Meter | None = None,
        clock: Clock = perf_counter,
    ) -> None:
        self._logger = logger or logging.getLogger("grounded_ops.telemetry")
        self._tracer = tracer or trace.get_tracer("grounded_ops")
        actual_meter = meter or metrics.get_meter("grounded_ops")
        self._requests = _counter(actual_meter)
        self._duration = _histogram(actual_meter)
        self._clock = clock

    def new_context(
        self,
        correlation_id: str | None = None,
        *,
        entry_point: EntryPoint,
    ) -> TelemetryContext:
        return TelemetryContext(_correlation_id(correlation_id), entry_point)

    def current_context(self) -> TelemetryContext | None:
        return _CURRENT.get()

    @contextmanager
    def bind(self, context: TelemetryContext) -> Iterator[TelemetryContext]:
        token = _CURRENT.set(context)
        try:
            yield context
        finally:
            _CURRENT.reset(token)

    def inject_carrier(self) -> dict[str, str]:
        context = self.current_context()
        if context is None:
            raise RuntimeError("telemetry context is not bound")
        carrier = {
            "x-correlation-id": context.correlation_id,
            "x-entry-point": context.entry_point.value,
        }
        propagate.inject(carrier)
        return carrier

    @contextmanager
    def bind_carrier(
        self,
        carrier: dict[str, str],
        *,
        default_entry_point: EntryPoint = EntryPoint.WORKER,
    ) -> Iterator[TelemetryContext]:
        entry_point = _entry_point(carrier.get("x-entry-point"), default_entry_point)
        context = self.new_context(
            carrier.get("x-correlation-id"), entry_point=entry_point
        )
        otel_token = otel_context.attach(propagate.extract(carrier))
        try:
            with self.bind(context):
                yield context
        finally:
            otel_context.detach(otel_token)

    @contextmanager
    def operation(
        self,
        component: TelemetryComponent,
        operation: TelemetryOperation,
        *,
        default_entry_point: EntryPoint = EntryPoint.INTERNAL,
    ) -> Iterator[Observation]:
        existing = self.current_context()
        context = existing or self.new_context(entry_point=default_entry_point)
        context_token = _CURRENT.set(context) if existing is None else None
        observation = Observation()
        started = self._clock()
        error_type: str | None = None
        try:
            with self._safe_span(operation) as span:
                _span_attribute(
                    span, "groundedops.correlation_id", context.correlation_id
                )
                _span_attribute(
                    span, "groundedops.entry_point", context.entry_point.value
                )
                _span_attribute(span, "groundedops.component", component.value)
                try:
                    yield observation
                except Exception as error:
                    observation.outcome = TelemetryOutcome.ERROR
                    error_type = type(error).__name__
                    _span_status(span, Status(StatusCode.ERROR))
                    _span_attribute(span, "error.type", error_type)
                    raise
                finally:
                    _span_attribute(
                        span, "groundedops.outcome", observation.outcome.value
                    )
                    if observation.outcome is TelemetryOutcome.ERROR:
                        _span_status(span, Status(StatusCode.ERROR))
        finally:
            duration_seconds = max(0.0, self._clock() - started)
            attributes = {
                "component": component.value,
                "operation": operation.value,
                "outcome": observation.outcome.value,
            }
            _best_effort(lambda: self._requests.add(1, attributes))
            _best_effort(lambda: self._duration.record(duration_seconds, attributes))
            payload: dict[str, object] = {
                "event": (
                    "operation_failed"
                    if observation.outcome is TelemetryOutcome.ERROR
                    else "operation_completed"
                ),
                "correlation_id": context.correlation_id,
                "entry_point": context.entry_point.value,
                **attributes,
                "duration_ms": round(duration_seconds * 1000, 3),
            }
            if error_type is not None:
                payload["error_type"] = error_type
            _best_effort(
                lambda: self._logger.log(
                    logging.ERROR
                    if observation.outcome is TelemetryOutcome.ERROR
                    else logging.INFO,
                    json.dumps(payload, sort_keys=True, separators=(",", ":")),
                )
            )
            if context_token is not None:
                _CURRENT.reset(context_token)

    @contextmanager
    def _safe_span(self, operation: TelemetryOperation) -> Iterator[Span | None]:
        try:
            manager = self._tracer.start_as_current_span(
                operation.value,
                record_exception=False,
                set_status_on_exception=False,
            )
            span = manager.__enter__()
        except Exception:
            yield None
            return
        business_error: BaseException | None = None
        try:
            yield span
        except BaseException as error:
            business_error = error
            raise
        finally:
            if business_error is None:
                _best_effort(lambda: manager.__exit__(None, None, None))
            else:
                _best_effort(
                    lambda: manager.__exit__(
                        type(business_error),
                        business_error,
                        business_error.__traceback__,
                    )
                )


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Create one safe correlation context for each HTTP request."""

    def __init__(self, app: ASGIApp, telemetry: Telemetry) -> None:
        super().__init__(app)
        self._telemetry = telemetry

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        context = self._telemetry.new_context(
            request.headers.get("x-correlation-id"), entry_point=EntryPoint.API
        )
        with self._telemetry.bind(context):
            with self._telemetry.operation(
                TelemetryComponent.API,
                TelemetryOperation.HTTP_REQUEST,
                default_entry_point=EntryPoint.API,
            ) as observation:
                response = await call_next(request)
                if response.status_code >= 500:
                    observation.outcome = TelemetryOutcome.ERROR
        response.headers["X-Correlation-ID"] = context.correlation_id
        return response


def _correlation_id(value: str | None) -> str:
    if value is None:
        return uuid4().hex
    try:
        return UUID(value).hex
    except (ValueError, AttributeError, TypeError):
        return uuid4().hex


def _entry_point(value: str | None, default: EntryPoint) -> EntryPoint:
    try:
        return EntryPoint(value) if value is not None else default
    except ValueError:
        return default


def _counter(meter: Meter) -> Counter | _NoOpCounter:
    try:
        return meter.create_counter(
            "grounded_ops_operations_total",
            description="Completed GroundedOps operations",
        )
    except Exception:
        return _NoOpCounter()


def _histogram(meter: Meter) -> Histogram | _NoOpHistogram:
    try:
        return meter.create_histogram(
            "grounded_ops_operation_duration_seconds",
            unit="s",
            description="GroundedOps operation latency",
        )
    except Exception:
        return _NoOpHistogram()


def _span_attribute(span: Span | None, name: str, value: str) -> None:
    if span is not None:
        _best_effort(lambda: span.set_attribute(name, value))


def _span_status(span: Span | None, status: Status) -> None:
    if span is not None:
        _best_effort(lambda: span.set_status(status))


def _best_effort(operation: Callable[[], object]) -> None:
    try:
        operation()
    except Exception:
        return
