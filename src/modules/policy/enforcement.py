"""Centralize authoritative document policy checks across every data path."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from domain.ingestion import validate_identifier
from modules.audit.recorder import AuditReason, AuditRecorder
from modules.policy.authorizer import Authorizer, Decision, Principal, ResourceAction


@dataclass(frozen=True)
class DocumentRef:
    tenant_id: str
    document_id: str
    document_version_id: str | None = None

    def __post_init__(self) -> None:
        validate_identifier(self.tenant_id)
        validate_identifier(self.document_id)
        if self.document_version_id is not None:
            validate_identifier(self.document_version_id)


@dataclass(frozen=True)
class DocumentPolicy:
    tenant_id: str
    source_id: str
    document_id: str
    document_version_id: str | None
    policy: tuple[str, ...]
    deleted: bool

    def __post_init__(self) -> None:
        validate_identifier(self.tenant_id)
        validate_identifier(self.source_id)
        validate_identifier(self.document_id)
        if self.document_version_id is not None:
            validate_identifier(self.document_version_id)
        if not self.policy:
            raise ValueError("invalid access policy")
        for entry in self.policy:
            validate_identifier(entry)


class DocumentPolicyStore(Protocol):
    def get_document_policies(
        self, tenant_id: str, references: tuple[DocumentRef, ...]
    ) -> Mapping[DocumentRef, DocumentPolicy]: ...


class ProjectionView(Protocol):
    @property
    def tenant_id(self) -> str: ...

    @property
    def source_id(self) -> str: ...

    @property
    def document_id(self) -> str: ...

    @property
    def document_version_id(self) -> str: ...

    @property
    def policy(self) -> tuple[str, ...]: ...


@dataclass(frozen=True)
class SearchScope:
    tenant_id: str
    access_policy: tuple[str, ...]


@dataclass(frozen=True)
class AuthorizedDocument:
    policy: DocumentPolicy
    decision: Decision


class PolicyViolation(RuntimeError):
    """A write attempted to diverge from authoritative policy metadata."""


class PolicyEnforcer:
    """Expose one small authorization seam backed by current metadata."""

    def __init__(
        self, store: DocumentPolicyStore, audit: AuditRecorder | None = None
    ) -> None:
        self._store = store
        self._authorizer = Authorizer()
        self._audit = audit or AuditRecorder.ephemeral()

    def search_scope(self, principal: Principal) -> SearchScope | None:
        if not principal.known:
            return None
        return SearchScope(
            principal.tenant_id,
            (
                "public",
                f"principal:{principal.id}",
                *(f"role:{role}" for role in principal.roles),
                *(f"group:{group}" for group in principal.groups),
            ),
        )

    def authorize_reads(
        self,
        principal: Principal,
        references: tuple[DocumentRef, ...],
        correlation_id: str | None = None,
    ) -> dict[DocumentRef, AuthorizedDocument]:
        correlation = correlation_id or uuid4().hex
        unique_references = tuple(dict.fromkeys(references))
        if not principal.known:
            for reference in unique_references:
                self._record_authorization(
                    principal,
                    reference,
                    False,
                    AuditReason.UNKNOWN_PRINCIPAL,
                    correlation,
                )
            return {}
        for reference in unique_references:
            if reference.tenant_id != principal.tenant_id:
                self._record_authorization(
                    principal,
                    reference,
                    False,
                    AuditReason.CROSS_TENANT,
                    correlation,
                )
        scoped = tuple(
            reference
            for reference in unique_references
            if reference.tenant_id == principal.tenant_id
        )
        if not scoped:
            return {}
        policies = self._store.get_document_policies(principal.tenant_id, scoped)
        authorized: dict[DocumentRef, AuthorizedDocument] = {}
        for reference in scoped:
            policy = policies.get(reference)
            if (
                policy is None
                or policy.deleted
                or policy.tenant_id != reference.tenant_id
                or policy.document_id != reference.document_id
                or policy.document_version_id != reference.document_version_id
            ):
                self._record_authorization(
                    principal,
                    reference,
                    False,
                    AuditReason.POLICY_DENIED,
                    correlation,
                )
                continue
            decision = self._authorizer.authorize(
                principal,
                ResourceAction(
                    policy.tenant_id,
                    policy.document_id,
                    "read",
                    policy.policy,
                ),
            )
            self._record_authorization(
                principal,
                reference,
                decision.allowed,
                AuditReason(decision.reason.value),
                correlation,
            )
            if decision.allowed:
                authorized[reference] = AuthorizedDocument(policy, decision)
        return authorized

    def _record_authorization(
        self,
        principal: Principal,
        reference: DocumentRef,
        allowed: bool,
        reason: AuditReason,
        correlation_id: str,
    ) -> None:
        self._audit.record_authorization(
            tenant_id=principal.tenant_id,
            principal_id=principal.id,
            document_id=reference.document_id,
            allowed=allowed,
            reason=reason,
            correlation_id=correlation_id,
        )

    def validate_projections(self, projections: tuple[ProjectionView, ...]) -> None:
        by_tenant: dict[str, list[ProjectionView]] = {}
        for projection in projections:
            by_tenant.setdefault(projection.tenant_id, []).append(projection)
        for tenant_id, scoped in by_tenant.items():
            references = tuple(
                DocumentRef(item.tenant_id, item.document_id, item.document_version_id)
                for item in scoped
            )
            policies = self._store.get_document_policies(tenant_id, references)
            for projection, reference in zip(scoped, references, strict=True):
                policy = policies.get(reference)
                if (
                    policy is None
                    or policy.deleted
                    or policy.tenant_id != projection.tenant_id
                    or policy.source_id != projection.source_id
                    or policy.document_id != projection.document_id
                    or policy.document_version_id != projection.document_version_id
                    or set(policy.policy) != set(projection.policy)
                ):
                    raise PolicyViolation("projection unauthorized")

    def validate_removal(self, reference: DocumentRef) -> None:
        if reference.document_version_id is not None:
            raise PolicyViolation("removal unauthorized")
        policy = self._store.get_document_policies(
            reference.tenant_id, (reference,)
        ).get(reference)
        if policy is None:
            raise PolicyViolation("removal unauthorized")
