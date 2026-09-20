"""Small, bounded tool contracts for multi-hop investigations.

The investigation graph depends on these interfaces instead of calling search,
metadata or incident stores directly.  This keeps authorization, limits and
provider error handling in one place.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol, TypeVar

from domain.ingestion import DocumentVersion, validate_identifier
from modules.policy.authorizer import Principal
from modules.policy.enforcement import AuthorizedDocument, DocumentRef, PolicyEnforcer
from modules.retrieval.retriever import Evidence, EvidenceSet, QueryContext

T = TypeVar("T")


class ToolErrorCode(StrEnum):
    """Stable error categories exposed to the investigation graph."""

    UNKNOWN_PRINCIPAL = "unknown_principal"
    INVALID_INPUT = "invalid_input"
    POLICY_DENIED = "policy_denied"
    TIMEOUT = "timeout"
    BACKEND_UNAVAILABLE = "backend_unavailable"


class InvestigationToolError(RuntimeError):
    """Redacted, machine-readable failure from a bounded tool."""

    def __init__(self, code: ToolErrorCode) -> None:
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True)
class ToolLimits:
    """Hard per-call limits owned by the application, not by the model."""

    timeout_seconds: float = 2.0
    max_results: int = 10

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0 or self.timeout_seconds > 60:
            raise ValueError("timeout must be between 0 and 60 seconds")
        if not 1 <= self.max_results <= 100:
            raise ValueError("max_results must be between 1 and 100")


@dataclass(frozen=True)
class RetrieveRequest:
    query: str
    limit: int = 5
    include_historical: bool = False

    def __post_init__(self) -> None:
        if not self.query.strip() or len(self.query) > 1000:
            raise ValueError("invalid retrieval query")
        if not 1 <= self.limit <= 100:
            raise ValueError("invalid retrieval limit")


@dataclass(frozen=True)
class CompareVersionsRequest:
    document_id: str
    left_version_id: str
    right_version_id: str

    def __post_init__(self) -> None:
        for value in (
            self.document_id,
            self.left_version_id,
            self.right_version_id,
        ):
            validate_identifier(value)
        if self.left_version_id == self.right_version_id:
            raise ValueError("version comparison requires two versions")


@dataclass(frozen=True)
class IncidentSearchRequest:
    query: str
    limit: int = 5

    def __post_init__(self) -> None:
        if not self.query.strip() or len(self.query) > 1000:
            raise ValueError("invalid incident query")
        if not 1 <= self.limit <= 100:
            raise ValueError("invalid incident limit")


class Retriever(Protocol):
    def retrieve(self, context: QueryContext) -> EvidenceSet: ...


class VersionStore(Protocol):
    def get_version(
        self, tenant_id: str, version_id: str
    ) -> DocumentVersion | None: ...


@dataclass(frozen=True)
class IncidentRecord:
    """A searchable incident reference without raw source content."""

    incident_id: str
    tenant_id: str
    document_id: str
    document_version_id: str
    title: str
    summary: str
    occurred_at: datetime

    def __post_init__(self) -> None:
        for value in (
            self.incident_id,
            self.tenant_id,
            self.document_id,
            self.document_version_id,
        ):
            validate_identifier(value)
        if not self.title.strip() or not self.summary.strip():
            raise ValueError("incident text is required")
        if self.occurred_at.tzinfo is None:
            raise ValueError("incident timestamp requires timezone")


class IncidentSearch(Protocol):
    def search(
        self, tenant_id: str, query: str, limit: int
    ) -> tuple[IncidentRecord, ...]: ...


@dataclass(frozen=True)
class VersionChange:
    field: str
    before: str
    after: str


@dataclass(frozen=True)
class VersionComparison:
    document_id: str
    left_version_id: str
    right_version_id: str
    same_content: bool
    changes: tuple[VersionChange, ...]


class ToolTimeout(TimeoutError):
    """Internal timeout signal normalized by every tool."""


class TimeoutRunner(Protocol):
    def run(self, operation: Callable[[], T], timeout_seconds: float) -> T: ...


class ThreadTimeoutRunner:
    """Apply a response deadline to blocking adapter calls.

    Adapters should eventually expose native client timeouts; this runner is a
    safe seam for the first synchronous composition and keeps the graph bounded.
    """

    def run(self, operation: Callable[[], T], timeout_seconds: float) -> T:
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="investigation")
        future = executor.submit(operation)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as error:
            future.cancel()
            raise ToolTimeout from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)


class InvestigationTools:
    """Expose bounded retrieval, version comparison and incident search."""

    def __init__(
        self,
        retriever: Retriever,
        version_store: VersionStore,
        incident_search: IncidentSearch,
        enforcer: PolicyEnforcer,
        *,
        limits: ToolLimits | None = None,
        timeout_runner: TimeoutRunner | None = None,
    ) -> None:
        self._retriever = retriever
        self._version_store = version_store
        self._incident_search = incident_search
        self._enforcer = enforcer
        self._limits = limits or ToolLimits()
        self._timeout_runner = timeout_runner or ThreadTimeoutRunner()

    def retrieve(self, principal: Principal, request: RetrieveRequest) -> EvidenceSet:
        """Retrieve authorized evidence, never exceeding the configured bound."""
        self._require_principal(principal)
        self._require_limit(request.limit)
        try:
            result = self._run(
                lambda: self._retriever.retrieve(
                    QueryContext(
                        request.query,
                        principal,
                        request.limit,
                        include_historical=request.include_historical,
                    )
                )
            )
        except InvestigationToolError:
            raise
        except Exception as error:
            raise InvestigationToolError(ToolErrorCode.BACKEND_UNAVAILABLE) from error
        return _bounded_evidence(result, request.limit)

    def compare_versions(
        self, principal: Principal, request: CompareVersionsRequest
    ) -> VersionComparison:
        """Compare two versions only after both current policies allow reading."""
        self._require_principal(principal)
        references = (
            DocumentRef(
                principal.tenant_id, request.document_id, request.left_version_id
            ),
            DocumentRef(
                principal.tenant_id, request.document_id, request.right_version_id
            ),
        )
        try:
            left, right = self._run(
                lambda: self._authorized_versions(principal, references, request)
            )
        except InvestigationToolError:
            raise
        except Exception as error:
            raise InvestigationToolError(ToolErrorCode.BACKEND_UNAVAILABLE) from error
        if not _matches_version(
            left, request, request.left_version_id
        ) or not _matches_version(right, request, request.right_version_id):
            raise InvestigationToolError(ToolErrorCode.BACKEND_UNAVAILABLE)
        if left is None or right is None:
            raise InvestigationToolError(ToolErrorCode.BACKEND_UNAVAILABLE)
        return _compare(left, right)

    def search_incidents(
        self, principal: Principal, request: IncidentSearchRequest
    ) -> tuple[IncidentRecord, ...]:
        """Search incidents and reauthorize every document before returning it."""
        self._require_principal(principal)
        self._require_limit(request.limit)
        try:
            records, authorized = self._run(
                lambda: self._authorized_incidents(principal, request)
            )
        except InvestigationToolError:
            raise
        except Exception as error:
            raise InvestigationToolError(ToolErrorCode.BACKEND_UNAVAILABLE) from error
        return tuple(
            record
            for record in records
            if DocumentRef(
                record.tenant_id,
                record.document_id,
                record.document_version_id,
            )
            in authorized
        )

    def _run(self, operation: Callable[[], T]) -> T:
        try:
            return self._timeout_runner.run(operation, self._limits.timeout_seconds)
        except ToolTimeout as error:
            raise InvestigationToolError(ToolErrorCode.TIMEOUT) from error
        except InvestigationToolError:
            raise

    def _require_principal(self, principal: Principal) -> None:
        if not principal.known:
            raise InvestigationToolError(ToolErrorCode.UNKNOWN_PRINCIPAL)

    def _require_limit(self, limit: int) -> None:
        if limit > self._limits.max_results:
            raise InvestigationToolError(ToolErrorCode.INVALID_INPUT)

    def _require_authorized(
        self, principal: Principal, references: tuple[DocumentRef, ...]
    ) -> None:
        try:
            authorized = self._enforcer.authorize_reads(principal, references)
        except Exception as error:
            raise InvestigationToolError(ToolErrorCode.BACKEND_UNAVAILABLE) from error
        if len(authorized) != len(references):
            raise InvestigationToolError(ToolErrorCode.POLICY_DENIED)

    def _authorized_versions(
        self,
        principal: Principal,
        references: tuple[DocumentRef, ...],
        request: CompareVersionsRequest,
    ) -> tuple[DocumentVersion | None, DocumentVersion | None]:
        self._require_authorized(principal, references)
        return (
            self._version_store.get_version(
                principal.tenant_id, request.left_version_id
            ),
            self._version_store.get_version(
                principal.tenant_id, request.right_version_id
            ),
        )

    def _authorized_incidents(
        self, principal: Principal, request: IncidentSearchRequest
    ) -> tuple[tuple[IncidentRecord, ...], dict[DocumentRef, AuthorizedDocument]]:
        records = self._incident_search.search(
            principal.tenant_id, request.query, request.limit
        )
        bounded = records[: request.limit]
        references = tuple(
            DocumentRef(
                record.tenant_id,
                record.document_id,
                record.document_version_id,
            )
            for record in bounded
        )
        return bounded, self._enforcer.authorize_reads(principal, references)


def _bounded_evidence(result: EvidenceSet, limit: int) -> EvidenceSet:
    evidence: tuple[Evidence, ...] = result.evidence[:limit]
    return EvidenceSet(
        evidence,
        result.strategy,
        min(result.total, len(evidence)),
        result.took_ms,
        result.degraded,
        result.degradation_reason,
    )


def _matches_version(
    version: DocumentVersion | None,
    request: CompareVersionsRequest,
    expected_version_id: str,
) -> bool:
    return bool(
        version is not None
        and version.document_id == request.document_id
        and version.id == expected_version_id
    )


def _compare(left: DocumentVersion, right: DocumentVersion) -> VersionComparison:
    changes: list[VersionChange] = []
    fields = (
        ("source_version", left.source_version, right.source_version),
        ("content_hash", left.content_hash, right.content_hash),
        (
            "source_timestamp",
            left.source_timestamp.isoformat(),
            right.source_timestamp.isoformat(),
        ),
        ("parser_version", left.parser_version, right.parser_version),
    )
    for field, before, after in fields:
        if before != after:
            changes.append(VersionChange(field, before, after))
    return VersionComparison(
        left.document_id,
        left.id,
        right.id,
        left.content_hash == right.content_hash,
        tuple(changes),
    )
