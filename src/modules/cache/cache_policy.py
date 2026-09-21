"""Opaque cache keys and fail-open reuse for authorized versioned results."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from domain.answering import GroundedAnswer, Question, VerificationStatus
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.retrieval.retriever import EvidenceSet, QueryContext


class CacheBackend(Protocol):
    def get(self, key: str) -> object | None: ...

    def set(self, key: str, value: object, ttl_seconds: int) -> None: ...


class CacheKind(StrEnum):
    RETRIEVAL = "retrieval"
    ANSWER = "answer"


@dataclass(frozen=True)
class CacheVersions:
    """Monotonic versions supplied by authoritative application configuration."""

    policy: str
    corpus: str
    model: str

    def __post_init__(self) -> None:
        if any(
            not value.strip() or len(value) > 128
            for value in (self.policy, self.corpus, self.model)
        ):
            raise ValueError("invalid cache version")


@dataclass(frozen=True)
class CacheRecord:
    """Self-identifying value used to reject cross-key backend replay."""

    key: str
    kind: CacheKind
    value: object


@dataclass(frozen=True)
class CachedValue[T]:
    value: T
    cache_hit: bool


class VersionSafeCache:
    """Reuse only results bound to the complete freshness and access context."""

    def __init__(
        self,
        backend: CacheBackend,
        *,
        retrieval_ttl_seconds: int = 300,
        answer_ttl_seconds: int = 120,
    ) -> None:
        if retrieval_ttl_seconds <= 0 or answer_ttl_seconds <= 0:
            raise ValueError("invalid cache TTL")
        self._backend = backend
        self._retrieval_ttl_seconds = retrieval_ttl_seconds
        self._answer_ttl_seconds = answer_ttl_seconds

    def retrieval_key(self, context: QueryContext, versions: CacheVersions) -> str:
        return _key(
            CacheKind.RETRIEVAL,
            context.principal,
            versions,
            {
                "query": context.query,
                "limit": context.limit,
                "offset": context.offset,
                "source_ids": sorted(context.source_ids),
                "document_ids": sorted(context.document_ids),
                "include_historical": context.include_historical,
            },
        )

    def answer_key(
        self,
        question: Question,
        principal: Principal,
        versions: CacheVersions,
    ) -> str:
        return _key(
            CacheKind.ANSWER,
            principal,
            versions,
            {"question": question.text},
        )

    def retrieval(
        self,
        context: QueryContext,
        versions: CacheVersions,
        load: Callable[[], EvidenceSet],
    ) -> CachedValue[EvidenceSet]:
        key = self.retrieval_key(context, versions)
        record = self._read(key, CacheKind.RETRIEVAL)
        if (
            record is not None
            and isinstance(record.value, EvidenceSet)
            and _eligible_retrieval(record.value, context.principal)
        ):
            return CachedValue(record.value, True)

        value = load()
        if _eligible_retrieval(value, context.principal):
            self._write(
                CacheRecord(key, CacheKind.RETRIEVAL, value),
                self._retrieval_ttl_seconds,
            )
        return CachedValue(value, False)

    def answer(
        self,
        question: Question,
        principal: Principal,
        versions: CacheVersions,
        load: Callable[[], GroundedAnswer],
    ) -> CachedValue[GroundedAnswer]:
        key = self.answer_key(question, principal, versions)
        record = self._read(key, CacheKind.ANSWER)
        if (
            record is not None
            and isinstance(record.value, GroundedAnswer)
            and _eligible_answer(record.value, question, principal)
        ):
            return CachedValue(replace(record.value, question=question), True)

        value = load()
        if _eligible_answer(value, question, principal):
            self._write(
                CacheRecord(key, CacheKind.ANSWER, value),
                self._answer_ttl_seconds,
            )
        return CachedValue(value, False)

    def _read(self, key: str, kind: CacheKind) -> CacheRecord | None:
        try:
            value = self._backend.get(key)
        except Exception:
            return None
        if not isinstance(value, CacheRecord):
            return None
        if value.key != key or value.kind is not kind:
            return None
        return value

    def _write(self, record: CacheRecord, ttl_seconds: int) -> None:
        try:
            self._backend.set(record.key, record, ttl_seconds)
        except Exception:
            return


def _key(
    kind: CacheKind,
    principal: Principal,
    versions: CacheVersions,
    request: dict[str, object],
) -> str:
    payload = {
        "schema": 1,
        "kind": kind.value,
        "tenant_id": principal.tenant_id,
        "authorization": {
            "principal_id": principal.id,
            "roles": sorted(principal.roles),
            "groups": sorted(principal.groups),
            "known": principal.known,
        },
        "versions": {
            "policy": versions.policy,
            "corpus": versions.corpus,
            "model": versions.model,
        },
        "request": request,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode()
    return f"groundedops:v1:{kind.value}:{sha256(canonical).hexdigest()}"


def _eligible_retrieval(value: EvidenceSet, principal: Principal) -> bool:
    allowed_reasons = {
        AuthorizationReason.PUBLIC,
        AuthorizationReason.PRINCIPAL,
        AuthorizationReason.ROLE,
        AuthorizationReason.GROUP,
    }
    return (
        principal.known
        and not value.degraded
        and all(
            item.tenant_id == principal.tenant_id
            and item.authorization_reason in allowed_reasons
            for item in value.evidence
        )
    )


def _eligible_answer(
    value: GroundedAnswer,
    question: Question,
    principal: Principal,
) -> bool:
    return (
        principal.known
        and value.status is VerificationStatus.VERIFIED
        and value.question.text == question.text
    )
