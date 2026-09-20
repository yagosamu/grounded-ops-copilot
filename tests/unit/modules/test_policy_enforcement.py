"""One authoritative policy seam protects reads and index projections."""

from dataclasses import dataclass

import pytest

from modules.policy.authorizer import AuthorizationReason, Principal
from modules.policy.enforcement import (
    DocumentPolicy,
    DocumentRef,
    PolicyEnforcer,
    PolicyViolation,
)


class MemoryPolicyStore:
    def __init__(self, policies: tuple[DocumentPolicy, ...]) -> None:
        self._policies = {
            DocumentRef(
                item.tenant_id, item.document_id, item.document_version_id
            ): item
            for item in policies
        }
        self.requests: list[tuple[str, tuple[DocumentRef, ...]]] = []

    def get_document_policies(
        self, tenant_id: str, references: tuple[DocumentRef, ...]
    ) -> dict[DocumentRef, DocumentPolicy]:
        self.requests.append((tenant_id, references))
        return {
            reference: policy
            for reference in references
            if (policy := self._policies.get(reference)) is not None
        }


def policy(
    document_id: str,
    access: tuple[str, ...],
    *,
    tenant_id: str = "alpha",
    source_id: str = "source-1",
    version_id: str | None = "version-1",
    deleted: bool = False,
) -> DocumentPolicy:
    return DocumentPolicy(
        tenant_id,
        source_id,
        document_id,
        version_id,
        access,
        deleted,
    )


@dataclass(frozen=True)
class Projection:
    tenant_id: str
    source_id: str
    document_id: str
    document_version_id: str
    policy: tuple[str, ...]


@pytest.mark.unit
def test_authorizes_metadata_in_batch_without_querying_cross_tenant_ids() -> None:
    store = MemoryPolicyStore(
        (
            policy("allowed", ("role:engineer",)),
            policy("denied", ("group:oncall",)),
            policy("foreign", ("public",), tenant_id="beta"),
        )
    )
    enforcer = PolicyEnforcer(store)
    principal = Principal("alice", "alpha", ("engineer",), ())
    allowed = DocumentRef("alpha", "allowed", "version-1")
    denied = DocumentRef("alpha", "denied", "version-1")
    foreign = DocumentRef("beta", "foreign", "version-1")

    result = enforcer.authorize_reads(principal, (allowed, denied, foreign))

    assert tuple(result) == (allowed,)
    assert result[allowed].decision.reason is AuthorizationReason.ROLE
    assert result[allowed].policy.policy == ("role:engineer",)
    assert store.requests == [("alpha", (allowed, denied))]


@pytest.mark.unit
def test_current_metadata_revokes_stale_index_permissions() -> None:
    reference = DocumentRef("alpha", "document-1", "version-1")
    store = MemoryPolicyStore((policy("document-1", ("role:finance",)),))
    enforcer = PolicyEnforcer(store)

    stale_reader = enforcer.authorize_reads(
        Principal("alice", "alpha", ("engineer",), ()), (reference,)
    )
    current_reader = enforcer.authorize_reads(
        Principal("bob", "alpha", ("finance",), ()), (reference,)
    )

    assert stale_reader == {}
    assert current_reader[reference].decision.reason is AuthorizationReason.ROLE


@pytest.mark.unit
def test_projection_must_match_authoritative_identity_version_and_policy() -> None:
    store = MemoryPolicyStore((policy("document-1", ("role:engineer",)),))
    enforcer = PolicyEnforcer(store)
    valid = Projection(
        "alpha", "source-1", "document-1", "version-1", ("role:engineer",)
    )

    enforcer.validate_projections((valid,))

    invalid = (
        Projection("beta", "source-1", "document-1", "version-1", ("public",)),
        Projection("alpha", "forged", "document-1", "version-1", ("role:engineer",)),
        Projection("alpha", "source-1", "document-1", "missing", ("role:engineer",)),
        Projection("alpha", "source-1", "document-1", "version-1", ("public",)),
    )
    for projection in invalid:
        with pytest.raises(PolicyViolation, match="projection unauthorized"):
            enforcer.validate_projections((projection,))


@pytest.mark.unit
def test_deleted_documents_cannot_be_read_but_can_be_removed_from_index() -> None:
    read_ref = DocumentRef("alpha", "document-1", "version-1")
    remove_ref = DocumentRef("alpha", "document-1")
    store = MemoryPolicyStore(
        (
            policy("document-1", ("public",), deleted=True),
            policy("document-1", ("public",), version_id=None, deleted=True),
        )
    )
    enforcer = PolicyEnforcer(store)

    result = enforcer.authorize_reads(Principal("alice", "alpha", (), ()), (read_ref,))
    enforcer.validate_removal(remove_ref)

    assert result == {}
