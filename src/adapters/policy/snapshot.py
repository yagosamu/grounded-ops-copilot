"""Immutable policy metadata for deterministic offline evaluation."""

from modules.policy.enforcement import DocumentPolicy, DocumentRef


class SnapshotPolicyStore:
    def __init__(self, policies: tuple[DocumentPolicy, ...]) -> None:
        self._policies = {
            DocumentRef(
                item.tenant_id, item.document_id, item.document_version_id
            ): item
            for item in policies
        }

    def get_document_policies(
        self, tenant_id: str, references: tuple[DocumentRef, ...]
    ) -> dict[DocumentRef, DocumentPolicy]:
        return {
            reference: policy
            for reference in references
            if reference.tenant_id == tenant_id
            and (policy := self._policies.get(reference)) is not None
        }
