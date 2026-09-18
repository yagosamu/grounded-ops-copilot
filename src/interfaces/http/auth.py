"""Strict Bearer authentication for protected HTTP interfaces."""

from collections.abc import Callable, Mapping
from typing import Annotated

import jwt
from fastapi import HTTPException, Security, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt.exceptions import PyJWTError

from modules.policy.authorizer import Principal

PrincipalDependency = Callable[..., Principal]

_BEARER = HTTPBearer(auto_error=False, bearerFormat="JWT")
_REQUIRED_CLAIMS = ("exp", "iss", "aud", "sub", "tenant_id")
_MAX_IDENTITY_VALUE_LENGTH = 128
_MAX_MEMBERSHIPS = 100


class _InvalidPrincipalClaims(ValueError):
    """Token claims cannot form a safe principal context."""


class JwtAuthenticator:
    """Validate a JWT and build trusted principal context.

    Verification material and accepted algorithms are injected together so an
    untrusted token can never select its own validation algorithm.
    """

    def __init__(
        self,
        *,
        verification_key: str | bytes,
        issuer: str,
        audience: str,
        algorithms: tuple[str, ...],
        leeway_seconds: int = 0,
    ) -> None:
        if not verification_key:
            raise ValueError("verification key is required")
        if not issuer or not audience:
            raise ValueError("issuer and audience are required")
        if not algorithms or any(
            not algorithm or algorithm.lower() == "none" for algorithm in algorithms
        ):
            raise ValueError("at least one signed JWT algorithm is required")
        hmac_algorithms = [algorithm.startswith("HS") for algorithm in algorithms]
        if any(hmac_algorithms) and not all(hmac_algorithms):
            raise ValueError("symmetric and asymmetric JWT algorithms cannot be mixed")
        if leeway_seconds < 0:
            raise ValueError("JWT leeway cannot be negative")
        self._verification_key = verification_key
        self._issuer = issuer
        self._audience = audience
        self._algorithms = algorithms
        self._leeway_seconds = leeway_seconds

    def __call__(
        self,
        credentials: Annotated[
            HTTPAuthorizationCredentials | None, Security(_BEARER)
        ] = None,
    ) -> Principal:
        if credentials is None:
            raise _unauthorized("authentication required")
        return self.authenticate(credentials.credentials)

    def authenticate(self, token: str) -> Principal:
        """Return immutable principal context or a generic authentication failure."""
        try:
            claims = jwt.decode(
                token,
                self._verification_key,
                algorithms=list(self._algorithms),
                audience=self._audience,
                issuer=self._issuer,
                leeway=self._leeway_seconds,
                options={"require": list(_REQUIRED_CLAIMS)},
            )
            return _principal_from(claims)
        except (PyJWTError, _InvalidPrincipalClaims) as error:
            raise _unauthorized("invalid credentials") from error


def _principal_from(claims: Mapping[str, object]) -> Principal:
    return Principal(
        id=_identity_value(claims, "sub"),
        tenant_id=_identity_value(claims, "tenant_id"),
        roles=_memberships(claims, "roles"),
        groups=_memberships(claims, "groups"),
    )


def _identity_value(claims: Mapping[str, object], name: str) -> str:
    value = claims.get(name)
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > _MAX_IDENTITY_VALUE_LENGTH
    ):
        raise _InvalidPrincipalClaims(f"invalid {name} claim")
    return value


def _memberships(claims: Mapping[str, object], name: str) -> tuple[str, ...]:
    value = claims.get(name, [])
    if not isinstance(value, list) or len(value) > _MAX_MEMBERSHIPS:
        raise _InvalidPrincipalClaims(f"invalid {name} claim")
    memberships: list[str] = []
    for item in value:
        if (
            not isinstance(item, str)
            or not item.strip()
            or len(item) > _MAX_IDENTITY_VALUE_LENGTH
        ):
            raise _InvalidPrincipalClaims(f"invalid {name} claim")
        if item not in memberships:
            memberships.append(item)
    return tuple(memberships)


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )
