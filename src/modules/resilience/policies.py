"""Bounded circuit breaking without hidden retries."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from threading import Lock
from time import monotonic
from typing import Protocol


class Clock(Protocol):
    def __call__(self) -> float: ...


class CircuitState(StrEnum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitOpenError(RuntimeError):
    """A safe fail-fast signal for an unavailable dependency."""


class CircuitBreaker:
    """Block calls after consecutive failures and allow one recovery probe."""

    def __init__(
        self,
        *,
        failure_threshold: int = 2,
        recovery_timeout_seconds: float = 30.0,
        clock: Clock = monotonic,
    ) -> None:
        if failure_threshold <= 0 or recovery_timeout_seconds <= 0:
            raise ValueError("invalid circuit breaker policy")
        self._failure_threshold = failure_threshold
        self._recovery_timeout_seconds = recovery_timeout_seconds
        self._clock = clock
        self._state = CircuitState.CLOSED
        self._failures = 0
        self._opened_at: float | None = None
        self._lock = Lock()

    @property
    def state(self) -> CircuitState:
        with self._lock:
            return self._state

    @contextmanager
    def attempt(self) -> Iterator[None]:
        self._acquire()
        try:
            yield
        except Exception:
            self._record_failure()
            raise
        else:
            self._record_success()

    def _acquire(self) -> None:
        with self._lock:
            if self._state is CircuitState.CLOSED:
                return
            if self._state is CircuitState.HALF_OPEN:
                raise CircuitOpenError("dependency circuit is open")
            if self._opened_at is None:
                raise RuntimeError("open circuit requires timestamp")
            elapsed = self._clock() - self._opened_at
            if elapsed < self._recovery_timeout_seconds:
                raise CircuitOpenError("dependency circuit is open")
            self._state = CircuitState.HALF_OPEN

    def _record_success(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failures = 0
            self._opened_at = None

    def _record_failure(self) -> None:
        with self._lock:
            if self._state is CircuitState.HALF_OPEN:
                self._open()
                return
            self._failures += 1
            if self._failures >= self._failure_threshold:
                self._open()

    def _open(self) -> None:
        self._state = CircuitState.OPEN
        self._opened_at = self._clock()


def validate_timeout(timeout_seconds: float, *, maximum_seconds: float) -> None:
    if timeout_seconds <= 0 or timeout_seconds > maximum_seconds:
        raise ValueError("invalid dependency timeout")


def validate_attempts(attempts: int, *, maximum_attempts: int) -> None:
    if attempts <= 0 or attempts > maximum_attempts:
        raise ValueError("invalid dependency attempt bound")
