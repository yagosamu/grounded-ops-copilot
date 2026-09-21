"""Circuit breaking is bounded, deterministic and safe under concurrency."""

from collections.abc import Callable

import pytest

from modules.resilience.policies import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitState,
    validate_attempts,
    validate_timeout,
)

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_opens_after_threshold_and_blocks_calls_until_recovery_window() -> None:
    clock = Clock()
    breaker = CircuitBreaker(
        failure_threshold=2,
        recovery_timeout_seconds=10,
        clock=clock,
    )
    calls = 0

    def fail() -> None:
        nonlocal calls
        calls += 1
        raise RuntimeError("dependency failed")

    for _ in range(2):
        with pytest.raises(RuntimeError, match="dependency failed"):
            with breaker.attempt():
                fail()

    with pytest.raises(CircuitOpenError, match="dependency circuit is open"):
        with breaker.attempt():
            calls += 1

    assert calls == 2
    assert breaker.state is CircuitState.OPEN


def test_allows_one_probe_and_closes_after_successful_recovery() -> None:
    clock = Clock()
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=5,
        clock=clock,
    )

    with pytest.raises(RuntimeError):
        with breaker.attempt():
            raise RuntimeError("failed")

    clock.now = 5
    with breaker.attempt():
        assert breaker.state is CircuitState.HALF_OPEN
        with pytest.raises(CircuitOpenError):
            with breaker.attempt():
                pass

    assert breaker.state is CircuitState.CLOSED
    with breaker.attempt():
        pass


def test_failed_probe_reopens_for_a_full_recovery_window() -> None:
    clock = Clock()
    breaker = CircuitBreaker(
        failure_threshold=1,
        recovery_timeout_seconds=3,
        clock=clock,
    )

    for instant in (0.0, 3.0):
        clock.now = instant
        with pytest.raises(RuntimeError):
            with breaker.attempt():
                raise RuntimeError("still unavailable")

    clock.now = 5.9
    with pytest.raises(CircuitOpenError):
        with breaker.attempt():
            pass

    clock.now = 6.0
    with breaker.attempt():
        pass
    assert breaker.state is CircuitState.CLOSED


@pytest.mark.parametrize(
    ("failure_threshold", "recovery_timeout_seconds"),
    [(0, 1.0), (1, 0.0)],
)
def test_rejects_unbounded_or_nonpositive_policy(
    failure_threshold: int,
    recovery_timeout_seconds: float,
) -> None:
    with pytest.raises(ValueError, match="invalid circuit breaker policy"):
        CircuitBreaker(
            failure_threshold=failure_threshold,
            recovery_timeout_seconds=recovery_timeout_seconds,
        )


@pytest.mark.parametrize(
    ("operation", "message"),
    [
        (lambda: validate_timeout(10.1, maximum_seconds=10), "timeout"),
        (lambda: validate_attempts(3, maximum_attempts=2), "attempt bound"),
    ],
)
def test_rejects_dependency_bounds_that_can_create_retry_storms(
    operation: Callable[[], None],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        operation()
