"""Authenticated streaming Ask interface with verified terminal answers."""

import asyncio
import json
from collections.abc import AsyncIterator, Generator, Iterator
from dataclasses import dataclass
from time import sleep
from typing import Annotated, Protocol
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from domain.answering import AnswerUsage, GroundedAnswer, Question
from interfaces.http.auth import PrincipalDependency
from modules.answering.abstention import AbstentionDecider, AbstentionDecision
from modules.answering.context_packer import ContextPacker, PackedContext
from modules.answering.generator import (
    GenerationCompleted,
    GenerationDelta,
    GenerationFailure,
    GenerationProviderError,
    GenerationRequest,
    StreamingGenerationProvider,
    build_grounded_draft,
)
from modules.answering.verifier import CitationVerifier
from modules.policy.authorizer import Principal
from modules.retrieval.retriever import (
    EvidenceSet,
    QueryContext,
    RetrievalUnavailable,
)


class Retriever(Protocol):
    def retrieve(self, context: QueryContext) -> EvidenceSet: ...


@dataclass(frozen=True)
class AskEvent:
    payload: dict[str, object]


class AskSession:
    def __init__(self, events: Generator[AskEvent]) -> None:
        self._events = events
        self.cancelled = False

    def __iter__(self) -> Iterator[AskEvent]:
        return self._events

    def cancel(self) -> None:
        if not self.cancelled:
            self.cancelled = True
            self._events.close()


class AskExecutor(Protocol):
    def start(self, question: Question, principal: Principal) -> AskSession: ...


class AskService:
    def __init__(
        self,
        retriever: Retriever,
        packer: ContextPacker,
        provider: StreamingGenerationProvider,
        verifier: CitationVerifier,
        abstention: AbstentionDecider,
        *,
        max_generation_attempts: int = 2,
        retry_delays: tuple[float, ...] = (0.05,),
    ) -> None:
        if (
            max_generation_attempts <= 0
            or len(retry_delays) < max_generation_attempts - 1
            or any(delay < 0 for delay in retry_delays)
        ):
            raise ValueError("invalid ask retry policy")
        self._retriever = retriever
        self._packer = packer
        self._provider = provider
        self._verifier = verifier
        self._abstention = abstention
        self._max_generation_attempts = max_generation_attempts
        self._retry_delays = retry_delays

    def start(self, question: Question, principal: Principal) -> AskSession:
        session: AskSession

        def events() -> Generator[AskEvent]:
            yield from self._events(session, question, principal)

        session = AskSession(events())
        return session

    def _events(
        self, session: AskSession, question: Question, principal: Principal
    ) -> Iterator[AskEvent]:
        yield AskEvent({"type": "status", "status": "retrieving"})
        if session.cancelled:
            return
        try:
            evidence_set = self._retriever.retrieve(
                QueryContext(question.text, principal)
            )
        except RetrievalUnavailable:
            yield _failure("retrieval_unavailable", "retrieval temporarily unavailable")
            return
        context = self._packer.pack(evidence_set)
        early_abstention = self._abstention.decide(context)
        if early_abstention is not None:
            yield _final_abstention(
                early_abstention,
                question,
                AnswerUsage("none", 0, 0),
                evidence_set,
            )
            return
        yield AskEvent({"type": "status", "status": "generating"})
        completed = yield from self._generate(session, question, context)
        if completed is None:
            return
        try:
            draft = build_grounded_draft(question, context, completed.response)
        except GenerationProviderError as error:
            yield _generation_failure(error)
            return
        verification = self._verifier.verify(draft, evidence_set, principal)
        decision = self._abstention.decide(context, verification=verification)
        if decision is not None:
            yield _final_abstention(decision, question, draft.usage, evidence_set)
            return
        yield _final_answer(verification.answer, evidence_set)

    def _generate(
        self, session: AskSession, question: Question, context: PackedContext
    ) -> Generator[AskEvent, None, GenerationCompleted | None]:
        request = GenerationRequest(question, context)
        for attempt in range(self._max_generation_attempts):
            emitted_delta = False
            completed: GenerationCompleted | None = None
            try:
                for event in self._provider.stream(request):
                    if session.cancelled:
                        return None
                    if isinstance(event, GenerationDelta):
                        emitted_delta = True
                        yield AskEvent(
                            {
                                "type": "delta",
                                "status": "unverified",
                                "text": event.text,
                            }
                        )
                    else:
                        completed = event
            except GenerationProviderError as error:
                can_retry = (
                    error.retryable
                    and not emitted_delta
                    and attempt + 1 < self._max_generation_attempts
                )
                if can_retry:
                    sleep(self._retry_delays[attempt])
                    continue
                yield _generation_failure(error)
                return None
            if completed is None:
                yield _generation_failure(
                    GenerationProviderError(
                        GenerationFailure.INCOMPLETE, retryable=False
                    )
                )
                return None
            return completed
        return None


class AskBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


def create_ask_router(
    executor: AskExecutor, authenticate: PrincipalDependency
) -> APIRouter:
    router = APIRouter(prefix="/v1", tags=["answering"])

    @router.post("/ask")
    async def ask(
        body: AskBody,
        request: Request,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> StreamingResponse:
        question = Question(str(uuid4()), body.question)
        session = executor.start(question, principal)
        return StreamingResponse(
            _stream(request, session), media_type="application/x-ndjson"
        )

    return router


async def _stream(request: Request, session: AskSession) -> AsyncIterator[bytes]:
    try:
        for event in session:
            if await request.is_disconnected():
                return
            yield (json.dumps(event.payload, separators=(",", ":")) + "\n").encode()
            await asyncio.sleep(0)
    finally:
        session.cancel()


def _final_answer(answer: GroundedAnswer, evidence_set: EvidenceSet) -> AskEvent:
    return AskEvent(
        {
            "type": "final",
            "status": answer.status.value,
            "answer": " ".join(claim.text for claim in answer.claims),
            "claims": _claims(answer),
            "sources": _sources(answer, evidence_set),
            "usage": _usage(answer.usage),
            "abstention": None,
            "degraded": _degraded(evidence_set),
        }
    )


def _final_abstention(
    decision: AbstentionDecision,
    question: Question,
    usage: AnswerUsage,
    evidence_set: EvidenceSet,
) -> AskEvent:
    answer = decision.to_answer(question, usage)
    return AskEvent(
        {
            "type": "final",
            "status": answer.status.value,
            "answer": decision.message,
            "claims": [],
            "sources": [],
            "usage": _usage(answer.usage),
            "abstention": decision.reason.value,
            "degraded": _degraded(evidence_set),
        }
    )


def _failure(code: str, message: str) -> AskEvent:
    return AskEvent(
        {
            "type": "final",
            "status": "failed",
            "answer": None,
            "claims": [],
            "sources": [],
            "usage": None,
            "abstention": None,
            "error": {"code": code, "message": message},
            "degraded": {"active": False, "mode": None, "reason": None},
        }
    )


def _generation_failure(error: GenerationProviderError) -> AskEvent:
    code = f"generation_{error.failure.value}"
    return _failure(code, "generation temporarily unavailable")


def _claims(answer: GroundedAnswer) -> list[dict[str, object]]:
    return [
        {
            "text": claim.text,
            "citations": [
                {
                    "evidence_id": citation.evidence_id,
                    "document_version_id": citation.document_version_id,
                    "span": list(citation.span),
                    "resolved": citation.resolved,
                }
                for citation in claim.citations
            ],
        }
        for claim in answer.claims
    ]


def _sources(
    answer: GroundedAnswer, evidence_set: EvidenceSet
) -> list[dict[str, object]]:
    cited = {
        (citation.evidence_id, citation.document_version_id, citation.span)
        for claim in answer.claims
        for citation in claim.citations
    }
    return [
        {
            "evidence_id": item.chunk_id,
            "source_id": item.source_id,
            "document_id": item.document_id,
            "document_version_id": item.document_version_id,
            "span": list(item.span),
        }
        for item in evidence_set.evidence
        if (item.chunk_id, item.document_version_id, item.span) in cited
    ]


def _usage(usage: AnswerUsage) -> dict[str, object]:
    return {
        "model": usage.model,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
    }


def _degraded(evidence_set: EvidenceSet) -> dict[str, object]:
    return {
        "active": evidence_set.degraded,
        "mode": "bm25_only" if evidence_set.degraded else None,
        "reason": evidence_set.degradation_reason,
    }
