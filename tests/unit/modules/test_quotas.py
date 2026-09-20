"""SEC-01/OPS-01 quota policy is deterministic and fail-closed."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from modules.policy.authorizer import Principal
from modules.policy.quotas import (
    DEFAULT_QUOTA_POLICY,
    InMemoryQuotaBackend,
    QuotaBackendError,
    QuotaEnforcer,
    QuotaLimits,
    QuotaOperation,
    QuotaOutcome,
)

pytestmark = pytest.mark.unit
START = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
KEY = sha256(b"quota-test-key").digest()
PRINCIPAL = Principal("alice", "alpha", (), ())


def limiter(
    backend: InMemoryQuotaBackend | None = None,
    *,
    search: QuotaLimits | None = None,
) -> QuotaEnforcer:
    policy = dict(DEFAULT_QUOTA_POLICY)
    policy[QuotaOperation.SEARCH] = search or QuotaLimits(2, 60, 2)
    return QuotaEnforcer(backend or InMemoryQuotaBackend(), policy, KEY)


def test_burst_limit_returns_retry_hint_and_reset_allows_the_next_window() -> None:
    subject = limiter(search=QuotaLimits(2, 60, 2))

    first = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)
    second = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)
    blocked = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)
    reset = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START.replace(minute=1))

    assert first.outcome is QuotaOutcome.ALLOWED
    assert second.outcome is QuotaOutcome.ALLOWED
    assert blocked.outcome is QuotaOutcome.RATE_LIMITED
    assert blocked.retry_after_seconds == 60
    assert reset.outcome is QuotaOutcome.ALLOWED


def test_tenant_budget_is_shared_across_principals() -> None:
    subject = limiter(search=QuotaLimits(4, 60, 8))
    other = Principal("bob", "alpha", (), ())

    first = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)
    second = subject.acquire(other, QuotaOperation.SEARCH, START)
    third = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)
    fourth = subject.acquire(other, QuotaOperation.SEARCH, START)

    assert first.outcome is QuotaOutcome.ALLOWED
    assert second.outcome is QuotaOutcome.ALLOWED
    assert third.outcome is QuotaOutcome.ALLOWED
    assert fourth.outcome is QuotaOutcome.ALLOWED
    blocked = subject.acquire(other, QuotaOperation.SEARCH, START)

    assert blocked.outcome is QuotaOutcome.RATE_LIMITED


def test_concurrency_reservation_is_released_before_another_request_runs() -> None:
    subject = limiter(search=QuotaLimits(10, 60, 1))

    lease = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)
    blocked = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)
    released = subject.release(lease.lease)
    allowed = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)

    assert lease.outcome is QuotaOutcome.ALLOWED
    assert lease.lease is not None
    assert blocked.outcome is QuotaOutcome.CONCURRENT_LIMITED
    assert released is True
    assert allowed.outcome is QuotaOutcome.ALLOWED


class FailingBackend(InMemoryQuotaBackend):
    def reserve(self, *args: object, **kwargs: object) -> object:
        raise QuotaBackendError("private backend detail")


def test_backend_failure_denies_without_exposing_provider_details() -> None:
    subject = limiter(FailingBackend())

    decision = subject.acquire(PRINCIPAL, QuotaOperation.SEARCH, START)

    assert decision.outcome is QuotaOutcome.BACKEND_UNAVAILABLE
    assert decision.retry_after_seconds == 1
    assert "private" not in repr(decision)


def test_default_policy_has_explicit_limits_for_all_workloads() -> None:
    assert DEFAULT_QUOTA_POLICY == {
        QuotaOperation.SEARCH: QuotaLimits(60, 60, 8),
        QuotaOperation.INGESTION: QuotaLimits(10, 60, 2),
        QuotaOperation.INVESTIGATION: QuotaLimits(5, 60, 1),
    }
