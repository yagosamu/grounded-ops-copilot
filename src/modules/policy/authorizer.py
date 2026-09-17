"""Resolve explicit tenant and document access decisions outside the model."""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True)
class Principal:
    id: str
    tenant_id: str
    roles: tuple[str, ...]
    groups: tuple[str, ...]
    known: bool = True


@dataclass(frozen=True)
class ResourceAction:
    tenant_id: str
    document_id: str
    action: str
    policy: tuple[str, ...]


class AuthorizationReason(StrEnum):
    PUBLIC = "public"
    PRINCIPAL = "principal"
    ROLE = "role"
    GROUP = "group"
    POLICY_DENIED = "policy_denied"
    UNKNOWN_PRINCIPAL = "unknown_principal"
    CROSS_TENANT = "cross_tenant"
    ACTION_DENIED = "action_denied"


@dataclass(frozen=True)
class Decision:
    allowed: bool
    reason: AuthorizationReason
    document_id: str


class Authorizer:
    def authorize(self, principal: Principal, resource: ResourceAction) -> Decision:
        if not principal.known:
            return Decision(
                False, AuthorizationReason.UNKNOWN_PRINCIPAL, resource.document_id
            )
        if principal.tenant_id != resource.tenant_id:
            return Decision(
                False, AuthorizationReason.CROSS_TENANT, resource.document_id
            )
        if resource.action != "read":
            return Decision(
                False, AuthorizationReason.ACTION_DENIED, resource.document_id
            )
        policy = set(resource.policy)
        if "public" in policy:
            return Decision(True, AuthorizationReason.PUBLIC, resource.document_id)
        if f"principal:{principal.id}" in policy:
            return Decision(True, AuthorizationReason.PRINCIPAL, resource.document_id)
        if any(f"role:{role}" in policy for role in principal.roles):
            return Decision(True, AuthorizationReason.ROLE, resource.document_id)
        if any(f"group:{group}" in policy for group in principal.groups):
            return Decision(True, AuthorizationReason.GROUP, resource.document_id)
        return Decision(False, AuthorizationReason.POLICY_DENIED, resource.document_id)
