"""Content-addressed, conditionally created raw and normalized artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from hashlib import sha256
from typing import TYPE_CHECKING, Protocol, cast

from botocore.exceptions import BotoCoreError, ClientError

from domain.ingestion import validate_identifier
from modules.ingestion.errors import ArtifactFailure
from modules.resilience.policies import (
    CircuitBreaker,
    CircuitOpenError,
    validate_attempts,
    validate_timeout,
)

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


class _S3Config(Protocol):
    connect_timeout: float
    read_timeout: float
    retries: dict[str, int | str]


class _S3Meta(Protocol):
    config: _S3Config


class _ConfiguredS3Client(Protocol):
    meta: _S3Meta


class ArtifactError(ArtifactFailure):
    """A safe artifact failure, without provider configuration or contents."""


@dataclass(frozen=True)
class ArtifactRef:
    tenant_id: str
    kind: str
    digest: str

    def __post_init__(self) -> None:
        validate_identifier(self.tenant_id)
        if self.kind not in ("raw", "normalized"):
            raise ValueError("invalid artifact kind")
        if not re.fullmatch("[a-f0-9]{64}", self.digest):
            raise ValueError("invalid artifact digest")

    @property
    def key(self) -> str:
        return f"{self.tenant_id}/{self.kind}/{self.digest}"


class DocumentStore:
    MAX_BYTES = 10 * 1024 * 1024

    def __init__(
        self,
        client: S3Client,
        bucket: str,
        circuit_breaker: CircuitBreaker | None = None,
    ) -> None:
        _validate_transport(client)
        self.client = client
        self.bucket = bucket
        self._circuit_breaker = circuit_breaker or CircuitBreaker()

    def put(self, tenant_id: str, kind: str, content: bytes) -> ArtifactRef:
        if len(content) > self.MAX_BYTES:
            raise ArtifactError("artifact exceeds size limit")
        reference = ArtifactRef(tenant_id, kind, sha256(content).hexdigest())
        try:
            with self._circuit_breaker.attempt():
                try:
                    self.client.put_object(
                        Bucket=self.bucket,
                        Key=reference.key,
                        Body=content,
                        IfNoneMatch="*",
                    )
                except ClientError as error:
                    if error.response["Error"]["Code"] != "PreconditionFailed":
                        raise
        except (ClientError, BotoCoreError, CircuitOpenError):
            raise ArtifactError("artifact unavailable") from None
        self.get(tenant_id, reference)
        return reference

    def get(self, tenant_id: str, reference: ArtifactRef) -> bytes:
        if tenant_id != reference.tenant_id:
            raise ArtifactError("artifact unavailable")
        try:
            with self._circuit_breaker.attempt():
                response = self.client.get_object(Bucket=self.bucket, Key=reference.key)
                stream = response["Body"]
                try:
                    content = stream.read(self.MAX_BYTES + 1)
                finally:
                    stream.close()
        except (ClientError, BotoCoreError, CircuitOpenError):
            raise ArtifactError("artifact unavailable") from None
        if (
            len(content) > self.MAX_BYTES
            or sha256(content).hexdigest() != reference.digest
        ):
            raise ArtifactError("artifact integrity failed")
        return content


def _validate_transport(client: S3Client) -> None:
    config = cast(_ConfiguredS3Client, client).meta.config
    validate_timeout(config.connect_timeout, maximum_seconds=2.0)
    validate_timeout(config.read_timeout, maximum_seconds=5.0)
    attempts = config.retries.get("total_max_attempts")
    if not isinstance(attempts, int):
        raise ValueError("object store client requires a total attempt bound")
    validate_attempts(attempts, maximum_attempts=1)
