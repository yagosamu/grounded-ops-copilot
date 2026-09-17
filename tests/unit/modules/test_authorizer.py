"""Authorization decisions are explicit and independent of retrieval/model code."""

import pytest

from modules.policy.authorizer import (
    AuthorizationReason,
    Authorizer,
    Principal,
    ResourceAction,
)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("policy", "reason"),
    [
        (("public",), AuthorizationReason.PUBLIC),
        (("principal:alice",), AuthorizationReason.PRINCIPAL),
        (("role:engineer",), AuthorizationReason.ROLE),
        (("group:oncall",), AuthorizationReason.GROUP),
    ],
)
def test_allows_each_supported_policy_match(
    policy: tuple[str, ...], reason: AuthorizationReason
) -> None:
    principal = Principal(
        id="alice",
        tenant_id="alpha",
        roles=("engineer",),
        groups=("oncall",),
    )

    decision = Authorizer().authorize(
        principal, ResourceAction("alpha", "document-1", "read", policy)
    )

    assert decision.allowed is True
    assert decision.reason is reason
    assert decision.document_id == "document-1"


@pytest.mark.unit
def test_denies_when_no_document_policy_matches() -> None:
    principal = Principal("alice", "alpha", ("engineer",), ("oncall",))

    decision = Authorizer().authorize(
        principal,
        ResourceAction("alpha", "document-1", "read", ("role:finance",)),
    )

    assert decision.allowed is False
    assert decision.reason is AuthorizationReason.POLICY_DENIED


@pytest.mark.unit
def test_denies_unknown_principal_explicitly() -> None:
    principal = Principal("missing", "alpha", (), (), known=False)

    decision = Authorizer().authorize(
        principal, ResourceAction("alpha", "document-1", "read", ("public",))
    )

    assert decision.allowed is False
    assert decision.reason is AuthorizationReason.UNKNOWN_PRINCIPAL


@pytest.mark.unit
def test_denies_cross_tenant_before_matching_policy() -> None:
    principal = Principal("alice", "alpha", ("engineer",), ())

    decision = Authorizer().authorize(
        principal,
        ResourceAction("beta", "document-2", "read", ("role:engineer",)),
    )

    assert decision.allowed is False
    assert decision.reason is AuthorizationReason.CROSS_TENANT


@pytest.mark.unit
def test_denies_unsupported_action() -> None:
    principal = Principal("alice", "alpha", ("admin",), ())

    decision = Authorizer().authorize(
        principal, ResourceAction("alpha", "document-1", "write", ("public",))
    )

    assert decision.allowed is False
    assert decision.reason is AuthorizationReason.ACTION_DENIED
