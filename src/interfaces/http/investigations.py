"""Owned asynchronous investigation lifecycle routes."""

from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Header, HTTPException, Response
from fastapi import status as http_status
from pydantic import BaseModel, Field

from domain.answering import Question
from interfaces.http.auth import PrincipalDependency
from modules.investigation.workflow import (
    ConcurrentInvestigationUpdate,
    InvestigationBudget,
    InvestigationNotFound,
    InvestigationNotTerminal,
    InvestigationReport,
    InvestigationState,
    InvestigationStatus,
    InvestigationTask,
    InvestigationWorkflow,
)
from modules.policy.authorizer import Principal

DEFAULT_INVESTIGATION_BUDGET = InvestigationBudget(
    max_steps=12,
    max_duration_seconds=120,
    max_tokens=20_000,
    max_tool_calls=8,
    max_retrieval_attempts=5,
)


class InvestigationDispatcher(Protocol):
    """Submit a durable investigation id to an idempotent background queue."""

    def enqueue(self, investigation_id: str) -> None: ...


class InvestigationExecutor(Protocol):
    def start(
        self, task: InvestigationTask, budget: InvestigationBudget
    ) -> InvestigationState: ...

    def get(self, investigation_id: str) -> InvestigationState: ...

    def cancel(self, investigation_id: str) -> InvestigationState: ...

    def report(self, investigation_id: str) -> InvestigationReport: ...


class InvestigationDispatchUnavailable(RuntimeError):
    """The background queue did not accept an investigation."""


class IdempotencyConflict(RuntimeError):
    """An idempotency key was reused for a different request."""


class InvestigationAlreadyTerminal(RuntimeError):
    """A completed investigation cannot be cancelled."""


@dataclass(frozen=True)
class Submission:
    state: InvestigationState
    replayed: bool


class InvestigationLifecycle:
    """Create and expose investigations without executing work in HTTP requests."""

    def __init__(
        self,
        workflow: InvestigationWorkflow,
        dispatcher: InvestigationDispatcher,
        *,
        budget: InvestigationBudget = DEFAULT_INVESTIGATION_BUDGET,
    ) -> None:
        self._workflow = workflow
        self._dispatcher = dispatcher
        self._budget = budget

    def submit(
        self, question_text: str, idempotency_key: str, principal: Principal
    ) -> Submission:
        investigation_id = _investigation_id(principal, idempotency_key)
        replayed = False
        try:
            state = self._workflow.start(
                InvestigationTask(
                    investigation_id,
                    Question(f"question-{investigation_id}", question_text),
                    principal,
                ),
                self._budget,
            )
        except ConcurrentInvestigationUpdate:
            state = self._owned(investigation_id, principal)
            if state.task.question.text != question_text:
                raise IdempotencyConflict(
                    "idempotency key was reused with a different request"
                ) from None
            replayed = True
        try:
            self._dispatcher.enqueue(investigation_id)
        except Exception as error:
            raise InvestigationDispatchUnavailable(
                "investigation queue is unavailable"
            ) from error
        return Submission(state, replayed)

    def status(self, investigation_id: str, principal: Principal) -> InvestigationState:
        return self._owned(investigation_id, principal)

    def cancel(self, investigation_id: str, principal: Principal) -> InvestigationState:
        current = self._owned(investigation_id, principal)
        if current.terminal and current.status is not InvestigationStatus.CANCELLED:
            raise InvestigationAlreadyTerminal("investigation is already terminal")
        cancelled = self._workflow.cancel(investigation_id)
        if cancelled.status is not InvestigationStatus.CANCELLED:
            raise InvestigationAlreadyTerminal("investigation is already terminal")
        return cancelled

    def report(
        self, investigation_id: str, principal: Principal
    ) -> InvestigationReport:
        self._owned(investigation_id, principal)
        return self._workflow.report(investigation_id)

    def _owned(self, investigation_id: str, principal: Principal) -> InvestigationState:
        state = self._workflow.get(investigation_id)
        owner = state.task.principal
        if owner.tenant_id != principal.tenant_id or owner.id != principal.id:
            raise InvestigationNotFound("investigation not found")
        return state


class InvestigationAPI(Protocol):
    def submit(
        self, question_text: str, idempotency_key: str, principal: Principal
    ) -> Submission: ...

    def status(
        self, investigation_id: str, principal: Principal
    ) -> InvestigationState: ...

    def cancel(
        self, investigation_id: str, principal: Principal
    ) -> InvestigationState: ...

    def report(
        self, investigation_id: str, principal: Principal
    ) -> InvestigationReport: ...


class InvestigationBody(BaseModel):
    question: str = Field(min_length=1, max_length=1000)


class ProgressResponse(BaseModel):
    steps: int
    tokens_used: int
    tool_calls: int
    retrieval_attempts: int


class InvestigationResponse(BaseModel):
    investigation_id: str
    status: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    progress: ProgressResponse
    stop_reason: str | None
    report_available: bool


def create_investigation_router(
    lifecycle: InvestigationAPI,
    authenticate: PrincipalDependency,
) -> APIRouter:
    router = APIRouter(prefix="/v1/investigations", tags=["investigations"])

    @router.post(
        "",
        response_model=InvestigationResponse,
        status_code=http_status.HTTP_202_ACCEPTED,
    )
    def create(
        body: InvestigationBody,
        response: Response,
        principal: Annotated[Principal, Depends(authenticate)],
        idempotency_key: Annotated[
            str,
            Header(alias="Idempotency-Key", min_length=1, max_length=128),
        ],
    ) -> InvestigationResponse:
        try:
            submission = lifecycle.submit(body.question, idempotency_key, principal)
        except IdempotencyConflict as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except InvestigationDispatchUnavailable as error:
            raise HTTPException(
                status_code=503, detail="investigation temporarily unavailable"
            ) from error
        response.headers["Idempotency-Replayed"] = str(submission.replayed).lower()
        return _state_response(submission.state)

    @router.get("/{investigation_id}", response_model=InvestigationResponse)
    def get_status(
        investigation_id: str,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> InvestigationResponse:
        try:
            return _state_response(lifecycle.status(investigation_id, principal))
        except InvestigationNotFound as error:
            raise _not_found(error) from error

    @router.post(
        "/{investigation_id}/cancel",
        response_model=InvestigationResponse,
        status_code=http_status.HTTP_202_ACCEPTED,
    )
    def cancel(
        investigation_id: str,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> InvestigationResponse:
        try:
            return _state_response(lifecycle.cancel(investigation_id, principal))
        except InvestigationNotFound as error:
            raise _not_found(error) from error
        except InvestigationAlreadyTerminal as error:
            raise HTTPException(status_code=409, detail=str(error)) from error

    @router.get("/{investigation_id}/report")
    def report(
        investigation_id: str,
        principal: Annotated[Principal, Depends(authenticate)],
    ) -> dict[str, object]:
        try:
            investigation_report = lifecycle.report(investigation_id, principal)
        except InvestigationNotFound as error:
            raise _not_found(error) from error
        except InvestigationNotTerminal as error:
            raise HTTPException(
                status_code=409, detail="investigation report is not ready"
            ) from error
        return _report_response(investigation_report)

    return router


def _investigation_id(principal: Principal, idempotency_key: str) -> str:
    digest = sha256(
        f"{principal.tenant_id}\0{principal.id}\0{idempotency_key}".encode()
    ).hexdigest()[:40]
    return f"inv-{digest}"


def _state_response(state: InvestigationState) -> InvestigationResponse:
    return InvestigationResponse(
        investigation_id=state.id,
        status=state.status.value,
        created_at=state.created_at,
        updated_at=state.updated_at,
        started_at=state.started_at,
        finished_at=state.finished_at,
        progress=ProgressResponse(
            steps=state.steps,
            tokens_used=state.tokens_used,
            tool_calls=state.tool_calls,
            retrieval_attempts=state.retrieval_attempts,
        ),
        stop_reason=state.stop_reason.value if state.stop_reason else None,
        report_available=state.terminal,
    )


def _report_response(report: InvestigationReport) -> dict[str, object]:
    return {
        "investigation_id": report.investigation_id,
        "summary": report.summary,
        "evidence": [
            {
                "evidence_id": item.chunk_id,
                "source_id": item.source_id,
                "document_id": item.document_id,
                "document_version_id": item.document_version_id,
                "span": list(item.span),
            }
            for item in report.evidence
        ],
        "incidents": [
            {
                "incident_id": item.incident_id,
                "document_id": item.document_id,
                "document_version_id": item.document_version_id,
                "title": item.title,
                "summary": item.summary,
                "occurred_at": item.occurred_at,
            }
            for item in report.incidents
        ],
        "comparisons": [
            {
                "document_id": item.document_id,
                "left_version_id": item.left_version_id,
                "right_version_id": item.right_version_id,
                "same_content": item.same_content,
                "changes": [
                    {
                        "field": change.field,
                        "before": change.before,
                        "after": change.after,
                    }
                    for change in item.changes
                ],
            }
            for item in report.comparisons
        ],
        "tool_actions": [
            {
                "tool": action.tool,
                "outcome": action.outcome.value,
                "duration_ms": action.duration_ms,
                "result_count": action.result_count,
                "error_code": action.error_code.value if action.error_code else None,
            }
            for action in report.tool_actions
        ],
        "unresolved_questions": list(report.unresolved_questions),
        "stop_reason": report.stop_reason.value,
        "usage": {
            "tokens": report.tokens_used,
            "cost_microusd": report.cost_microusd,
        },
        "duration_ms": report.duration_ms,
    }


def _not_found(error: Exception) -> HTTPException:
    return HTTPException(status_code=404, detail="investigation not found")
