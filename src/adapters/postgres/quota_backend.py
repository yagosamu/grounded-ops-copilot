"""Transactional PostgreSQL backend for shared quota reservations."""

from datetime import datetime

from sqlalchemy import Connection, text
from sqlalchemy.exc import SQLAlchemyError

from modules.policy.quotas import (
    BackendQuotaResult,
    QuotaBackendError,
    QuotaLease,
    QuotaLimits,
    QuotaOutcome,
    _retry_after,
    _window_start,
)


class PostgresQuotaBackend:
    """Reserve all scopes on a caller-owned transaction and row locks."""

    def __init__(self, connection: Connection) -> None:
        self._connection = connection

    def reserve(
        self,
        scopes: tuple[str, ...],
        limits: QuotaLimits,
        now: datetime,
    ) -> BackendQuotaResult:
        window_started = _window_start(now, limits.window_seconds)
        try:
            for scope in scopes:
                self._connection.execute(
                    text("""
                        INSERT INTO quota_buckets
                            (scope_key, window_started, request_count, active_count)
                        VALUES (:scope, :started, 0, 0)
                        ON CONFLICT (scope_key) DO NOTHING
                    """),
                    {"scope": scope, "started": window_started},
                )
            rows = [
                self._connection.execute(
                    text("""
                        SELECT scope_key, window_started, request_count, active_count
                        FROM quota_buckets
                        WHERE scope_key=:scope
                        FOR UPDATE
                    """),
                    {"scope": scope},
                )
                .mappings()
                .one()
                for scope in scopes
            ]
            for row in rows:
                reset = row["window_started"] != window_started
                request_count = 0 if reset else int(row["request_count"])
                active_count = 0 if reset else int(row["active_count"])
                if reset:
                    self._connection.execute(
                        text("""
                            UPDATE quota_buckets
                            SET window_started=:started, request_count=0,
                                active_count=0
                            WHERE scope_key=:scope
                        """),
                        {"scope": row["scope_key"], "started": window_started},
                    )
                if request_count >= limits.max_requests:
                    return BackendQuotaResult(
                        QuotaOutcome.RATE_LIMITED,
                        retry_after_seconds=_retry_after(window_started, limits, now),
                    )
                if active_count >= limits.max_concurrent:
                    return BackendQuotaResult(QuotaOutcome.CONCURRENT_LIMITED)
            for scope in scopes:
                self._connection.execute(
                    text("""
                        UPDATE quota_buckets
                        SET request_count=request_count+1,
                            active_count=active_count+1
                        WHERE scope_key=:scope
                    """),
                    {"scope": scope},
                )
        except SQLAlchemyError as error:
            raise QuotaBackendError("quota backend unavailable") from error
        return BackendQuotaResult(
            QuotaOutcome.ALLOWED,
            QuotaLease("postgres", scopes, window_started),
        )

    def release(self, lease: QuotaLease) -> None:
        try:
            for scope in lease.scopes:
                self._connection.execute(
                    text("""
                        UPDATE quota_buckets
                        SET active_count=GREATEST(active_count-1, 0)
                        WHERE scope_key=:scope AND window_started=:started
                    """),
                    {"scope": scope, "started": lease.window_started},
                )
        except SQLAlchemyError as error:
            raise QuotaBackendError("quota backend unavailable") from error
