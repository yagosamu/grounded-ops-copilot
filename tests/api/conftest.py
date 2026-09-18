"""Shared authentication fixtures for protected HTTP routes."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from secrets import token_bytes

import jwt
import pytest

from interfaces.http.auth import JwtAuthenticator

ISSUER = "https://identity.groundedops.test"
AUDIENCE = "grounded-ops-api"


@dataclass(frozen=True)
class AuthContext:
    authenticator: JwtAuthenticator
    signing_key: bytes

    def token(self, **overrides: object) -> str:
        now = datetime.now(UTC)
        claims: dict[str, object] = {
            "sub": "alice",
            "tenant_id": "alpha",
            "roles": ["engineer", "reader"],
            "groups": ["oncall"],
            "iss": ISSUER,
            "aud": AUDIENCE,
            "iat": now,
            "exp": now + timedelta(minutes=5),
        }
        claims.update(overrides)
        return jwt.encode(claims, self.signing_key, algorithm="HS256")

    def headers(self, **overrides: object) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token(**overrides)}"}


@pytest.fixture
def auth_context() -> AuthContext:
    signing_key = token_bytes(48)
    return AuthContext(
        JwtAuthenticator(
            verification_key=signing_key,
            issuer=ISSUER,
            audience=AUDIENCE,
            algorithms=("HS256",),
        ),
        signing_key,
    )
