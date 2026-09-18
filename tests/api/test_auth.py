"""Bearer credentials become trusted, immutable principal context."""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from secrets import token_bytes
from typing import Annotated

import jwt
import pytest
from conftest import AUDIENCE, ISSUER, AuthContext
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from interfaces.http.auth import JwtAuthenticator
from modules.policy.authorizer import Principal


def protected_client(authenticator: JwtAuthenticator) -> TestClient:
    app = FastAPI()

    @app.get("/protected")
    def protected(
        principal: Annotated[Principal, Depends(authenticator)],
    ) -> dict[str, object]:
        return {
            "id": principal.id,
            "tenant_id": principal.tenant_id,
            "roles": principal.roles,
            "groups": principal.groups,
            "known": principal.known,
        }

    return TestClient(app)


@pytest.mark.api
def test_valid_token_constructs_immutable_principal(auth_context: AuthContext) -> None:
    captured = auth_context.authenticator.authenticate(auth_context.token())

    assert captured == Principal("alice", "alpha", ("engineer", "reader"), ("oncall",))
    with pytest.raises(FrozenInstanceError):
        captured.tenant_id = "beta"

    response = protected_client(auth_context.authenticator).get(
        "/protected", headers=auth_context.headers()
    )
    assert response.status_code == 200
    assert response.json() == {
        "id": "alice",
        "tenant_id": "alpha",
        "roles": ["engineer", "reader"],
        "groups": ["oncall"],
        "known": True,
    }


@pytest.mark.api
@pytest.mark.parametrize(
    ("authorization", "expected_detail"),
    [
        (None, "authentication required"),
        ("Basic credentials", "authentication required"),
        ("Bearer not-a-jwt", "invalid credentials"),
    ],
)
def test_rejects_missing_or_malformed_credentials(
    auth_context: AuthContext,
    authorization: str | None,
    expected_detail: str,
) -> None:
    headers = {} if authorization is None else {"Authorization": authorization}

    response = protected_client(auth_context.authenticator).get(
        "/protected", headers=headers
    )

    assert response.status_code == 401
    assert response.json() == {"detail": expected_detail}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.api
@pytest.mark.parametrize(
    "overrides",
    [
        {"exp": datetime.now(UTC) - timedelta(seconds=1)},
        {"aud": "another-api"},
        {"iss": "https://untrusted.example"},
        {"tenant_id": ""},
        {"roles": "engineer"},
    ],
    ids=[
        "expired",
        "wrong-audience",
        "wrong-issuer",
        "empty-tenant",
        "invalid-roles",
    ],
)
def test_rejects_invalid_tokens_without_disclosing_cause(
    auth_context: AuthContext, overrides: dict[str, object]
) -> None:
    response = protected_client(auth_context.authenticator).get(
        "/protected", headers=auth_context.headers(**overrides)
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid credentials"}
    assert response.headers["www-authenticate"] == "Bearer"


@pytest.mark.api
def test_rejects_wrong_signature_and_disallowed_algorithm(
    auth_context: AuthContext,
) -> None:
    now = datetime.now(UTC)
    claims = {
        "sub": "alice",
        "tenant_id": "alpha",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "exp": now + timedelta(minutes=5),
    }
    wrong_signature = jwt.encode(claims, token_bytes(48), algorithm="HS256")
    wrong_algorithm = jwt.encode(claims, auth_context.signing_key, algorithm="HS384")
    api = protected_client(auth_context.authenticator)

    for token in (wrong_signature, wrong_algorithm):
        response = api.get("/protected", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid credentials"}
