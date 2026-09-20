"""Deterministic tenant/principal quotas with replaceable atomic backends."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from math import ceil
from threading import RLock
from typing import Protocol
from uuid import uuid4

from modules.policy.authorizer import Principal


class QuotaOperation(StrEnum):
    SEARCH = "search"
    INGESTION = "ingestion"
    INVESTIGATION = "investigation"


class QuotaOutcome(StrEnum):
    ALLOWED = "allowed"
    RATE_LIMITED = "rate_limited"
    CONCURRENT_LIMITED = "concurrent_limited"
    BACKEND_UNAVAILABLE = "backend_unavailable"


@dataclass(frozen=True)
class QuotaLimits:
    max_requests: int
    window_seconds: int
    max_concurrent: int

    def __post_init__(self) -> None:
        if self.max_requests <= 0:
            raise ValueError("max_requests must be positive")
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be positive")
        if self.max_concurrent <= 0:
            raise ValueError("max_concurrent must be positive")


@dataclass(frozen=True)
class QuotaLease:
    token: str
    scopes: tuple[str, ...]
    window_started: datetime


@dataclass(frozen=True)
class BackendQuotaResult:
    outcome: QuotaOutcome
    lease: QuotaLease | None = None
    retry_after_seconds: int = 1


@dataclass(frozen=True)
class QuotaDecision:
    outcome: QuotaOutcome
    lease: QuotaLease | None = None
    retry_after_seconds: int = 1


class QuotaBackendError(RuntimeError):
    """A quota backend cannot make a safe reservation decision."""


class QuotaBackend(Protocol):
    def reserve(
        self,
        scopes: tuple[str, ...],
        limits: QuotaLimits,
        now: datetime,
    ) -> BackendQuotaResult: ...

    def release(self, lease: QuotaLease) -> None: ...


@dataclass
class _Bucket:
    window_started: datetime
    request_count: int = 0
    active_count: int = 0


DEFAULT_QUOTA_POLICY: dict[QuotaOperation, QuotaLimits] = {
    QuotaOperation.SEARCH: QuotaLimits(60, 60, 8),
    QuotaOperation.INGESTION: QuotaLimits(10, 60, 2),
    QuotaOperation.INVESTIGATION: QuotaLimits(5, 60, 1),
}


class InMemoryQuotaBackend:
    """Thread-safe fixed-window backend for local execution and tests."""

    def __init__(self) -> None:
        self._lock = RLock()
        self._buckets: dict[str, _Bucket] = {}

    def reserve(
        self,
        scopes: tuple[str, ...],
        limits: QuotaLimits,
        now: datetime,
    ) -> BackendQuotaResult:
        if not scopes:
            raise QuotaBackendError("quota scopes are required")
        window_started = _window_start(now, limits.window_seconds)
        with self._lock:
            buckets = tuple(
                self._current_bucket(scope, window_started) for scope in scopes
            )
            if any(bucket.request_count >= limits.max_requests for bucket in buckets):
                return BackendQuotaResult(
                    QuotaOutcome.RATE_LIMITED,
                    retry_after_seconds=_retry_after(window_started, limits, now),
                )
            if any(bucket.active_count >= limits.max_concurrent for bucket in buckets):
                return BackendQuotaResult(QuotaOutcome.CONCURRENT_LIMITED)
            for bucket in buckets:
                bucket.request_count += 1
                bucket.active_count += 1
            return BackendQuotaResult(
                QuotaOutcome.ALLOWED,
                QuotaLease(uuid4().hex, scopes, window_started),
            )

    def release(self, lease: QuotaLease) -> None:
        with self._lock:
            for scope in lease.scopes:
                bucket = self._buckets.get(scope)
                if bucket is not None and bucket.window_started == lease.window_started:
                    bucket.active_count = max(bucket.active_count - 1, 0)

    def _current_bucket(self, scope: str, window_started: datetime) -> _Bucket:
        bucket = self._buckets.get(scope)
        if bucket is None or bucket.window_started != window_started:
            bucket = _Bucket(window_started)
            self._buckets[scope] = bucket
        return bucket


class QuotaEnforcer:
    """Reserve both tenant and principal budgets as one atomic decision."""

    def __init__(
        self,
        backend: QuotaBackend,
        policy: dict[QuotaOperation, QuotaLimits],
        redaction_key: bytes,
    ) -> None:
        if not redaction_key:
            raise ValueError("quota redaction key must not be empty")
        missing = set(QuotaOperation) - set(policy)
        if missing:
            raise ValueError("quota policy must define every operation")
        self._backend = backend
        self._policy = dict(policy)
        self._redaction_key = redaction_key

    def acquire(
        self,
        principal: Principal,
        operation: QuotaOperation,
        now: datetime | None = None,
    ) -> QuotaDecision:
        at = now or datetime.now(UTC)
        limits = self._policy[operation]
        scopes = (
            self._scope("tenant", principal.tenant_id, operation),
            self._scope("principal", principal.id, operation),
        )
        try:
            result = self._backend.reserve(scopes, limits, at)
        except QuotaBackendError:
            return QuotaDecision(
                QuotaOutcome.BACKEND_UNAVAILABLE, retry_after_seconds=1
            )
        return QuotaDecision(
            result.outcome,
            result.lease,
            result.retry_after_seconds,
        )

    def release(self, lease: QuotaLease | None) -> bool:
        if lease is None:
            return False
        try:
            self._backend.release(lease)
        except QuotaBackendError:
            return False
        return True

    def _scope(self, kind: str, value: str, operation: QuotaOperation) -> str:
        return hmac.new(
            self._redaction_key,
            f"{kind}\0{operation.value}\0{value}".encode(),
            sha256,
        ).hexdigest()


def _window_start(now: datetime, window_seconds: int) -> datetime:
    if now.tzinfo is None:
        raise ValueError("quota timestamps must be timezone-aware")
    timestamp = now.astimezone(UTC).timestamp()
    start = timestamp - (timestamp % window_seconds)
    return datetime.fromtimestamp(start, UTC)


def _retry_after(window_started: datetime, limits: QuotaLimits, now: datetime) -> int:
    window_end = window_started + timedelta(seconds=limits.window_seconds)
    return max(1, ceil((window_end - now.astimezone(UTC)).total_seconds()))
