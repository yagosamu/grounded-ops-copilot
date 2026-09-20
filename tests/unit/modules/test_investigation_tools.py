"""AGT-01 tools enforce bounds and policy before touching providers."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from adapters.policy.snapshot import SnapshotPolicyStore
from domain.ingestion import DocumentVersion
from modules.investigation.tools import (
    CompareVersionsRequest,
    IncidentRecord,
    IncidentSearchRequest,
    InvestigationToolError,
    InvestigationTools,
    RetrieveRequest,
    ToolErrorCode,
    ToolLimits,
    ToolTimeout,
    VersionComparison,
)
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.retriever import Evidence, EvidenceSet

pytestmark = pytest.mark.unit

PRINCIPAL = Principal("alice", "alpha", ("engineer",), ())
STAMP = datetime(2026, 9, 20, tzinfo=UTC)


class FakeRetriever:
    def __init__(self, result: EvidenceSet) -> None:
        self.result = result
        self.calls = 0

    def retrieve(self, context):
        self.calls += 1
        return self.result


class FakeVersionStore:
    def __init__(self, versions: tuple[DocumentVersion, ...]) -> None:
        self.versions = {version.id: version for version in versions}
        self.calls = 0

    def get_version(self, tenant_id: str, version_id: str):
        self.calls += 1
        version = self.versions.get(version_id)
        return version if version and version.tenant_id == tenant_id else None


class FakeIncidentSearch:
    def __init__(self, records: tuple[IncidentRecord, ...]) -> None:
        self.records = records
        self.calls: list[tuple[str, str, int]] = []

    def search(self, tenant_id: str, query: str, limit: int):
        self.calls.append((tenant_id, query, limit))
        return self.records


class InlineRunner:
    def __init__(self) -> None:
        self.timeouts: list[float] = []

    def run(self, operation, timeout_seconds: float):
        self.timeouts.append(timeout_seconds)
        return operation()


class AlwaysTimeoutRunner:
    def run(self, operation, timeout_seconds: float):
        raise ToolTimeout


class FailingRetriever(FakeRetriever):
    def retrieve(self, context):
        raise RuntimeError("https://private:token@search")


class FailingVersionStore(FakeVersionStore):
    def get_version(self, tenant_id: str, version_id: str):
        raise RuntimeError("postgres://private:secret@metadata")


class FailingIncidentSearch(FakeIncidentSearch):
    def search(self, tenant_id: str, query: str, limit: int):
        raise RuntimeError("https://private:token@incidents")


def evidence_set(*document_ids: str) -> EvidenceSet:
    return EvidenceSet(
        tuple(
            Evidence(
                f"chunk-{document_id}",
                "alpha",
                "source-1",
                document_id,
                f"version-{document_id}",
                0,
                f"evidence {document_id}",
                (0, 10),
                "a" * 64,
                "parser-v1",
                "chunker-v1",
                STAMP,
                True,
                float(index),
                AuthorizationReason.PUBLIC,
            )
            for index, document_id in enumerate(document_ids, 1)
        ),
        "bm25",
        len(document_ids),
        4,
    )


def version(version_id: str, source_version: str, content: str) -> DocumentVersion:
    return DocumentVersion(
        version_id,
        "alpha",
        "document-1",
        source_version,
        sha256(content.encode()).hexdigest(),
        STAMP,
        "parser-v1",
    )


def enforcer(*references: tuple[str, str]) -> PolicyEnforcer:
    return PolicyEnforcer(
        SnapshotPolicyStore(
            tuple(
                DocumentPolicy(
                    "alpha",
                    "source-1",
                    document_id,
                    version_id,
                    ("role:engineer",),
                    False,
                )
                for document_id, version_id in references
            )
        )
    )


def tools(
    retriever: FakeRetriever | None = None,
    versions: tuple[DocumentVersion, ...] = (),
    incidents: tuple[IncidentRecord, ...] = (),
    *,
    policy: PolicyEnforcer | None = None,
    runner: object | None = None,
    limits: ToolLimits | None = None,
) -> tuple[InvestigationTools, FakeRetriever, FakeVersionStore, FakeIncidentSearch]:
    actual_retriever = retriever or FakeRetriever(evidence_set("document-1"))
    version_store = FakeVersionStore(versions)
    incident_search = FakeIncidentSearch(incidents)
    subject = InvestigationTools(
        actual_retriever,
        version_store,
        incident_search,
        policy or enforcer(),
        limits=limits,
        timeout_runner=runner,
    )
    return subject, actual_retriever, version_store, incident_search


def test_retrieval_is_bounded_and_uses_the_configured_timeout() -> None:
    runner = InlineRunner()
    subject, retriever, _, _ = tools(
        FakeRetriever(evidence_set("document-1", "document-2", "document-3")),
        runner=runner,
        limits=ToolLimits(1.5, 2),
    )

    result = subject.retrieve(PRINCIPAL, RetrieveRequest("collector", 2))

    assert [item.document_id for item in result.evidence] == [
        "document-1",
        "document-2",
    ]
    assert result.total == 2
    assert retriever.calls == 1
    assert runner.timeouts == [1.5]


def test_unknown_principal_is_rejected_before_any_provider_call() -> None:
    subject, retriever, _, _ = tools()

    with pytest.raises(InvestigationToolError) as error:
        subject.retrieve(
            Principal("unknown", "alpha", (), (), known=False), RetrieveRequest("x")
        )

    assert error.value.code is ToolErrorCode.UNKNOWN_PRINCIPAL
    assert retriever.calls == 0
    assert "private" not in repr(error.value)


def test_version_comparison_reauthorizes_both_versions_and_hides_raw_refs() -> None:
    left = version("version-1", "r1", "before")
    right = version("version-2", "r2", "after")
    subject, _, store, _ = tools(
        versions=(left, right),
        policy=enforcer(("document-1", "version-1"), ("document-1", "version-2")),
    )

    result = subject.compare_versions(
        PRINCIPAL,
        CompareVersionsRequest("document-1", "version-1", "version-2"),
    )

    assert isinstance(result, VersionComparison)
    assert result.same_content is False
    assert {change.field for change in result.changes} == {
        "source_version",
        "content_hash",
    }
    assert store.calls == 2
    assert "raw_ref" not in repr(result)


def test_version_comparison_denies_when_one_version_is_not_authorized() -> None:
    left = version("version-1", "r1", "before")
    right = version("version-2", "r2", "after")
    subject, _, store, _ = tools(
        versions=(left, right),
        policy=enforcer(("document-1", "version-1")),
    )

    with pytest.raises(InvestigationToolError) as error:
        subject.compare_versions(
            PRINCIPAL,
            CompareVersionsRequest("document-1", "version-1", "version-2"),
        )

    assert error.value.code is ToolErrorCode.POLICY_DENIED
    assert store.calls == 0


def test_incident_search_filters_unauthorized_results_and_caps_backend_output() -> None:
    allowed = IncidentRecord(
        "incident-1",
        "alpha",
        "document-1",
        "version-1",
        "Collector outage",
        "summary",
        STAMP,
    )
    cross_tenant = IncidentRecord(
        "incident-2",
        "beta",
        "document-2",
        "version-2",
        "Private outage",
        "summary",
        STAMP,
    )
    subject, _, _, incident_search = tools(
        incidents=(allowed, cross_tenant),
        policy=enforcer(("document-1", "version-1")),
    )

    result = subject.search_incidents(PRINCIPAL, IncidentSearchRequest("outage", 1))

    assert result == (allowed,)
    assert incident_search.calls == [("alpha", "outage", 1)]


def test_timeout_is_normalized_without_provider_details() -> None:
    subject, _, _, _ = tools(runner=AlwaysTimeoutRunner())

    with pytest.raises(InvestigationToolError) as error:
        subject.retrieve(PRINCIPAL, RetrieveRequest("collector"))

    assert error.value.code is ToolErrorCode.TIMEOUT
    assert str(error.value) == "timeout"


def test_result_bound_is_rejected_before_retrieval() -> None:
    subject, retriever, _, _ = tools(limits=ToolLimits(2.0, 1))

    with pytest.raises(InvestigationToolError) as error:
        subject.retrieve(PRINCIPAL, RetrieveRequest("collector", 2))

    assert error.value.code is ToolErrorCode.INVALID_INPUT
    assert retriever.calls == 0


def test_backend_errors_are_typed_and_redacted_for_each_tool() -> None:
    retrieval, _, _, _ = tools(retriever=FailingRetriever(evidence_set()))
    version_subject, _, _, _ = tools(
        versions=(
            version("version-1", "r1", "before"),
            version("version-2", "r2", "after"),
        ),
        policy=enforcer(("document-1", "version-1"), ("document-1", "version-2")),
    )
    version_subject._version_store = FailingVersionStore(())
    incident_subject, _, _, _ = tools(
        incidents=(), policy=enforcer(("document-1", "version-1"))
    )
    incident_subject._incident_search = FailingIncidentSearch(())

    calls = (
        lambda: retrieval.retrieve(PRINCIPAL, RetrieveRequest("collector")),
        lambda: version_subject.compare_versions(
            PRINCIPAL,
            CompareVersionsRequest("document-1", "version-1", "version-2"),
        ),
        lambda: incident_subject.search_incidents(
            PRINCIPAL, IncidentSearchRequest("outage")
        ),
    )
    for call in calls:
        with pytest.raises(InvestigationToolError) as error:
            call()
        assert error.value.code is ToolErrorCode.BACKEND_UNAVAILABLE
        assert "private" not in repr(error.value)


def test_timeout_bound_applies_to_version_and_incident_tools() -> None:
    left = version("version-1", "r1", "before")
    right = version("version-2", "r2", "after")
    subject, _, _, _ = tools(
        versions=(left, right),
        incidents=(),
        policy=enforcer(("document-1", "version-1"), ("document-1", "version-2")),
        runner=AlwaysTimeoutRunner(),
    )

    for call in (
        lambda: subject.compare_versions(
            PRINCIPAL,
            CompareVersionsRequest("document-1", "version-1", "version-2"),
        ),
        lambda: subject.search_incidents(PRINCIPAL, IncidentSearchRequest("outage")),
    ):
        with pytest.raises(InvestigationToolError) as error:
            call()
        assert error.value.code is ToolErrorCode.TIMEOUT
