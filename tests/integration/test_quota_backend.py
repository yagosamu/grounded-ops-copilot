"""PostgreSQL quota reservations are shared and transactional."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest
from sqlalchemy import Engine

from adapters.postgres.quota_backend import PostgresQuotaBackend
from modules.policy.authorizer import Principal
from modules.policy.quotas import (
    QuotaEnforcer,
    QuotaLimits,
    QuotaOperation,
    QuotaOutcome,
)

pytestmark = pytest.mark.integration
START = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


def test_postgres_backend_shares_tenant_budget_and_releases_concurrency(
    database: Engine,
) -> None:
    policy = {
        QuotaOperation.SEARCH: QuotaLimits(2, 60, 1),
        QuotaOperation.INGESTION: QuotaLimits(2, 60, 1),
        QuotaOperation.INVESTIGATION: QuotaLimits(2, 60, 1),
    }
    first = Principal("alice", "alpha", (), ())
    second = Principal("bob", "alpha", (), ())
    with database.begin() as connection:
        subject = QuotaEnforcer(
            PostgresQuotaBackend(connection), policy, sha256(b"postgres-quota").digest()
        )
        reserved = subject.acquire(first, QuotaOperation.SEARCH, START)
        blocked_concurrently = subject.acquire(second, QuotaOperation.SEARCH, START)
        subject.release(reserved.lease)
        second_reserved = subject.acquire(second, QuotaOperation.SEARCH, START)
        exhausted = subject.acquire(first, QuotaOperation.SEARCH, START)

    assert reserved.outcome is QuotaOutcome.ALLOWED
    assert blocked_concurrently.outcome is QuotaOutcome.CONCURRENT_LIMITED
    assert second_reserved.outcome is QuotaOutcome.ALLOWED
    assert exhausted.outcome is QuotaOutcome.RATE_LIMITED
