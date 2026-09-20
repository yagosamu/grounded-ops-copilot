"""The public investigation-tool contract keeps tenant scope and bounds explicit."""

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from adapters.policy.snapshot import SnapshotPolicyStore
from domain.ingestion import DocumentVersion
from modules.investigation.tools import (
    CompareVersionsRequest,
    IncidentRecord,
    IncidentSearchRequest,
    InvestigationTools,
    RetrieveRequest,
    ToolLimits,
)
from modules.policy.authorizer import Principal
from modules.policy.enforcement import DocumentPolicy, PolicyEnforcer
from modules.retrieval.retriever import EvidenceSet

pytestmark = pytest.mark.integration


class ContractRetriever:
    def retrieve(self, context):
        assert context.principal.tenant_id == "alpha"
        assert context.limit == 1
        return EvidenceSet((), "contract", 0, 0)


class ContractVersionStore:
    def __init__(self) -> None:
        self.versions = {
            "v1": self._version("v1", "r1", "same"),
            "v2": self._version("v2", "r2", "same"),
        }

    @staticmethod
    def _version(version_id: str, revision: str, content: str) -> DocumentVersion:
        return DocumentVersion(
            version_id,
            "alpha",
            "doc-1",
            revision,
            sha256(content.encode()).hexdigest(),
            datetime(2026, 9, 20, tzinfo=UTC),
            "parser-v1",
        )

    def get_version(self, tenant_id: str, version_id: str):
        return self.versions.get(version_id)


class ContractIncidentSearch:
    def search(self, tenant_id: str, query: str, limit: int):
        return (
            IncidentRecord(
                "incident-1",
                tenant_id,
                "doc-1",
                "v1",
                "Collector outage",
                "Recovered after rollback",
                datetime(2026, 9, 20, tzinfo=UTC),
            ),
        )


class ContractRunner:
    def run(self, operation, timeout_seconds: float):
        assert timeout_seconds == 1.0
        return operation()


def test_tools_share_one_tenant_bound_contract() -> None:
    policy = PolicyEnforcer(
        SnapshotPolicyStore(
            (
                DocumentPolicy(
                    "alpha",
                    "source-1",
                    "doc-1",
                    "v1",
                    ("public",),
                    False,
                ),
                DocumentPolicy(
                    "alpha",
                    "source-1",
                    "doc-1",
                    "v2",
                    ("public",),
                    False,
                ),
            )
        )
    )
    subject = InvestigationTools(
        ContractRetriever(),
        ContractVersionStore(),
        ContractIncidentSearch(),
        policy,
        limits=ToolLimits(1.0, 3),
        timeout_runner=ContractRunner(),
    )
    principal = Principal("alice", "alpha", (), ())

    assert (
        subject.retrieve(principal, RetrieveRequest("collector", 1)).strategy
        == "contract"
    )
    comparison = subject.compare_versions(
        principal, CompareVersionsRequest("doc-1", "v1", "v2")
    )
    assert comparison.same_content is True
    assert (
        subject.search_incidents(principal, IncidentSearchRequest("outage", 1))[
            0
        ].tenant_id
        == "alpha"
    )
