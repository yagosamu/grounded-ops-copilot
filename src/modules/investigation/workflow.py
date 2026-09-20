"""Persisted state machine for bounded multi-hop investigations."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from functools import partial
from typing import Protocol, TypeVar

from domain.answering import Question
from domain.ingestion import validate_identifier
from modules.investigation.tools import (
    CompareVersionsRequest,
    IncidentRecord,
    IncidentSearchRequest,
    InvestigationToolError,
    RetrieveRequest,
    ToolErrorCode,
    VersionComparison,
)
from modules.policy.authorizer import Principal
from modules.retrieval.retriever import Evidence, EvidenceSet

T = TypeVar("T")


class InvestigationStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class InvestigationNode(StrEnum):
    PLAN = "plan"
    RETRIEVE = "retrieve"
    COMPARE = "compare"
    VERIFY = "verify"
    REPORT = "report"
    DONE = "done"


class StopReason(StrEnum):
    SUCCESS = "success"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    MAX_STEPS = "max_steps"
    TIMEOUT = "timeout"
    TOKEN_BUDGET = "token_budget"
    TOOL_BUDGET = "tool_budget"
    RETRIEVAL_BUDGET = "retrieval_budget"
    TOOL_FAILURE = "tool_failure"
    CANCELLED = "cancelled"
    WORKFLOW_FAILURE = "workflow_failure"


class ToolActionOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"


@dataclass(frozen=True)
class InvestigationBudget:
    max_steps: int
    max_duration_seconds: float
    max_tokens: int
    max_tool_calls: int
    max_retrieval_attempts: int

    def __post_init__(self) -> None:
        if self.max_steps <= 0:
            raise ValueError("max_steps must be positive")
        if self.max_duration_seconds <= 0:
            raise ValueError("max_duration_seconds must be positive")
        if self.max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        if self.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be positive")
        if self.max_retrieval_attempts <= 0:
            raise ValueError("max_retrieval_attempts must be positive")


@dataclass(frozen=True)
class InvestigationTask:
    id: str
    question: Question
    principal: Principal

    def __post_init__(self) -> None:
        validate_identifier(self.id)


@dataclass(frozen=True)
class InvestigationPlan:
    retrievals: tuple[RetrieveRequest, ...] = ()
    comparisons: tuple[CompareVersionsRequest, ...] = ()
    incidents: tuple[IncidentSearchRequest, ...] = ()


@dataclass(frozen=True)
class PlanResult:
    plan: InvestigationPlan
    tokens_used: int
    cost_microusd: int

    def __post_init__(self) -> None:
        _validate_usage(self.tokens_used, self.cost_microusd)


@dataclass(frozen=True)
class VerificationResult:
    sufficient: bool
    unresolved_questions: tuple[str, ...]
    tokens_used: int
    cost_microusd: int

    def __post_init__(self) -> None:
        _validate_usage(self.tokens_used, self.cost_microusd)
        if any(not question.strip() for question in self.unresolved_questions):
            raise ValueError("invalid unresolved question")


@dataclass(frozen=True)
class ReportResult:
    summary: str
    tokens_used: int
    cost_microusd: int

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("report summary is required")
        _validate_usage(self.tokens_used, self.cost_microusd)


@dataclass(frozen=True)
class ToolAction:
    node: InvestigationNode
    tool: str
    outcome: ToolActionOutcome
    duration_ms: int
    result_count: int
    error_code: ToolErrorCode | None = None

    def __post_init__(self) -> None:
        if not self.tool or self.duration_ms < 0 or self.result_count < 0:
            raise ValueError("invalid tool action")
        if (self.outcome is ToolActionOutcome.FAILED) != (self.error_code is not None):
            raise ValueError("invalid tool action outcome")


@dataclass(frozen=True)
class InvestigationState:
    task: InvestigationTask
    budget: InvestigationBudget
    status: InvestigationStatus
    node: InvestigationNode
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    revision: int = 0
    steps: int = 0
    tokens_used: int = 0
    cost_microusd: int = 0
    tool_calls: int = 0
    retrieval_attempts: int = 0
    plan: InvestigationPlan | None = None
    evidence: tuple[Evidence, ...] = ()
    incidents: tuple[IncidentRecord, ...] = ()
    comparisons: tuple[VersionComparison, ...] = ()
    tool_actions: tuple[ToolAction, ...] = ()
    unresolved_questions: tuple[str, ...] = ()
    summary: str | None = None
    pending_stop_reason: StopReason | None = None
    stop_reason: StopReason | None = None

    @property
    def id(self) -> str:
        return self.task.id

    @property
    def terminal(self) -> bool:
        return self.status in {
            InvestigationStatus.COMPLETED,
            InvestigationStatus.PARTIAL,
            InvestigationStatus.FAILED,
            InvestigationStatus.CANCELLED,
        }

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None or self.updated_at.tzinfo is None:
            raise ValueError("investigation timestamps require timezone")
        if self.started_at is not None and self.started_at.tzinfo is None:
            raise ValueError("investigation timestamps require timezone")
        if self.finished_at is not None and self.finished_at.tzinfo is None:
            raise ValueError("investigation timestamps require timezone")
        counters = (
            self.revision,
            self.steps,
            self.tokens_used,
            self.cost_microusd,
            self.tool_calls,
            self.retrieval_attempts,
        )
        if any(value < 0 for value in counters):
            raise ValueError("investigation counters cannot be negative")
        if self.terminal != (self.node is InvestigationNode.DONE):
            raise ValueError("terminal investigation must use done node")
        if self.terminal != (self.stop_reason is not None):
            raise ValueError("terminal investigation requires stop reason")


@dataclass(frozen=True)
class InvestigationReport:
    investigation_id: str
    summary: str
    evidence: tuple[Evidence, ...]
    incidents: tuple[IncidentRecord, ...]
    comparisons: tuple[VersionComparison, ...]
    tool_actions: tuple[ToolAction, ...]
    unresolved_questions: tuple[str, ...]
    stop_reason: StopReason
    tokens_used: int
    cost_microusd: int
    duration_ms: int


class Planner(Protocol):
    def plan(self, task: InvestigationTask) -> PlanResult: ...


class Toolbox(Protocol):
    def retrieve(
        self, principal: Principal, request: RetrieveRequest
    ) -> EvidenceSet: ...

    def compare_versions(
        self, principal: Principal, request: CompareVersionsRequest
    ) -> VersionComparison: ...

    def search_incidents(
        self, principal: Principal, request: IncidentSearchRequest
    ) -> tuple[IncidentRecord, ...]: ...


class Verifier(Protocol):
    def verify(self, state: InvestigationState) -> VerificationResult: ...


class Reporter(Protocol):
    def report(self, state: InvestigationState) -> ReportResult: ...


class InvestigationStore(Protocol):
    def create(self, state: InvestigationState) -> None: ...

    def get(self, investigation_id: str) -> InvestigationState | None: ...

    def save(self, state: InvestigationState, expected_revision: int) -> None: ...


class InvestigationNotFound(LookupError):
    """The requested investigation does not exist."""


class InvestigationNotTerminal(RuntimeError):
    """A report was requested before the workflow reached a terminal state."""


class ConcurrentInvestigationUpdate(RuntimeError):
    """Another worker persisted a newer investigation revision."""


class InMemoryInvestigationStore:
    """Revision-guarded store for tests and local single-process execution."""

    def __init__(self) -> None:
        self._states: dict[str, InvestigationState] = {}

    def create(self, state: InvestigationState) -> None:
        if state.id in self._states:
            raise ConcurrentInvestigationUpdate("investigation already exists")
        self._states[state.id] = state

    def get(self, investigation_id: str) -> InvestigationState | None:
        return self._states.get(investigation_id)

    def save(self, state: InvestigationState, expected_revision: int) -> None:
        current = self._states.get(state.id)
        if (
            current is None
            or current.revision != expected_revision
            or state.revision != expected_revision + 1
        ):
            raise ConcurrentInvestigationUpdate("investigation revision changed")
        self._states[state.id] = state


class InvestigationWorkflow:
    """Execute one persisted node at a time and stop on every configured budget."""

    def __init__(
        self,
        store: InvestigationStore,
        planner: Planner,
        tools: Toolbox,
        verifier: Verifier,
        reporter: Reporter,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._planner = planner
        self._tools = tools
        self._verifier = verifier
        self._reporter = reporter
        self._clock = clock or (lambda: datetime.now(UTC))

    def start(
        self, task: InvestigationTask, budget: InvestigationBudget
    ) -> InvestigationState:
        now = self._now()
        state = InvestigationState(
            task,
            budget,
            InvestigationStatus.QUEUED,
            InvestigationNode.PLAN,
            now,
            now,
        )
        self._store.create(state)
        return state

    def get(self, investigation_id: str) -> InvestigationState:
        state = self._store.get(investigation_id)
        if state is None:
            raise InvestigationNotFound("investigation not found")
        return state

    def run(self, investigation_id: str) -> InvestigationState:
        state = self.get(investigation_id)
        while not state.terminal:
            state = self.advance(investigation_id)
        return state

    def advance(self, investigation_id: str) -> InvestigationState:
        current = self.get(investigation_id)
        if current.terminal:
            return current
        state = current
        if state.status is InvestigationStatus.QUEUED:
            state = replace(
                state,
                status=InvestigationStatus.RUNNING,
                started_at=self._now(),
            )
        reason = self._budget_reason(state)
        if reason is not None:
            return self._persist(current, self._terminal(state, reason))
        try:
            advanced = {
                InvestigationNode.PLAN: self._plan,
                InvestigationNode.RETRIEVE: self._retrieve,
                InvestigationNode.COMPARE: self._compare,
                InvestigationNode.VERIFY: self._verify,
                InvestigationNode.REPORT: self._report,
            }[state.node](state)
        except Exception:
            advanced = self._terminal(state, StopReason.WORKFLOW_FAILURE)
        return self._persist(current, advanced)

    def cancel(self, investigation_id: str) -> InvestigationState:
        current = self.get(investigation_id)
        if current.terminal:
            return current
        return self._persist(current, self._terminal(current, StopReason.CANCELLED))

    def report(self, investigation_id: str) -> InvestigationReport:
        state = self.get(investigation_id)
        if not state.terminal or state.stop_reason is None:
            raise InvestigationNotTerminal("investigation report is not ready")
        started = state.started_at or state.created_at
        finished = state.finished_at or state.updated_at
        return InvestigationReport(
            state.id,
            state.summary or _default_summary(state.stop_reason),
            state.evidence,
            state.incidents,
            state.comparisons,
            state.tool_actions,
            state.unresolved_questions,
            state.stop_reason,
            state.tokens_used,
            state.cost_microusd,
            _duration_ms(started, finished),
        )

    def _plan(self, state: InvestigationState) -> InvestigationState:
        result = self._planner.plan(state.task)
        advanced = replace(
            state,
            plan=result.plan,
            node=InvestigationNode.RETRIEVE,
            steps=state.steps + 1,
            tokens_used=state.tokens_used + result.tokens_used,
            cost_microusd=state.cost_microusd + result.cost_microusd,
        )
        return self._after_node(advanced)

    def _retrieve(self, state: InvestigationState) -> InvestigationState:
        if state.plan is None:
            return self._terminal(state, StopReason.WORKFLOW_FAILURE)
        advanced = replace(state, steps=state.steps + 1)
        for retrieval_request in state.plan.retrievals:
            advanced, retrieval_result = self._tool_call(
                advanced,
                "retrieve",
                True,
                partial(
                    self._tools.retrieve,
                    state.task.principal,
                    retrieval_request,
                ),
            )
            if advanced.terminal or retrieval_result is None:
                return advanced
            advanced = replace(
                advanced,
                evidence=advanced.evidence + retrieval_result.evidence,
            )
        for incident_request in state.plan.incidents:
            advanced, incident_result = self._tool_call(
                advanced,
                "search_incidents",
                True,
                partial(
                    self._tools.search_incidents,
                    state.task.principal,
                    incident_request,
                ),
            )
            if advanced.terminal or incident_result is None:
                return advanced
            advanced = replace(
                advanced,
                incidents=advanced.incidents + incident_result,
            )
        return replace(advanced, node=InvestigationNode.COMPARE)

    def _compare(self, state: InvestigationState) -> InvestigationState:
        if state.plan is None:
            return self._terminal(state, StopReason.WORKFLOW_FAILURE)
        advanced = replace(state, steps=state.steps + 1)
        for comparison_request in state.plan.comparisons:
            advanced, result = self._tool_call(
                advanced,
                "compare_versions",
                False,
                partial(
                    self._tools.compare_versions,
                    state.task.principal,
                    comparison_request,
                ),
            )
            if advanced.terminal or result is None:
                return advanced
            advanced = replace(advanced, comparisons=advanced.comparisons + (result,))
        return replace(advanced, node=InvestigationNode.VERIFY)

    def _verify(self, state: InvestigationState) -> InvestigationState:
        result = self._verifier.verify(state)
        reason = state.pending_stop_reason
        if reason is None and not result.sufficient:
            reason = StopReason.INSUFFICIENT_EVIDENCE
        advanced = replace(
            state,
            node=InvestigationNode.REPORT,
            steps=state.steps + 1,
            tokens_used=state.tokens_used + result.tokens_used,
            cost_microusd=state.cost_microusd + result.cost_microusd,
            unresolved_questions=result.unresolved_questions,
            pending_stop_reason=reason or StopReason.SUCCESS,
        )
        return self._after_node(advanced)

    def _report(self, state: InvestigationState) -> InvestigationState:
        result = self._reporter.report(state)
        advanced = replace(
            state,
            steps=state.steps + 1,
            tokens_used=state.tokens_used + result.tokens_used,
            cost_microusd=state.cost_microusd + result.cost_microusd,
            summary=result.summary,
        )
        reason = self._budget_reason(advanced, include_steps=False)
        return self._terminal(
            advanced,
            reason or advanced.pending_stop_reason or StopReason.SUCCESS,
        )

    def _tool_call(
        self,
        state: InvestigationState,
        name: str,
        retrieval: bool,
        operation: Callable[[], T],
    ) -> tuple[InvestigationState, T | None]:
        budget_reason = self._tool_budget_reason(state, retrieval)
        if budget_reason is not None:
            return self._terminal(state, budget_reason), None
        started = self._now()
        calls = state.tool_calls + 1
        retrievals = state.retrieval_attempts + int(retrieval)
        try:
            result = operation()
        except InvestigationToolError as error:
            action = ToolAction(
                state.node,
                name,
                ToolActionOutcome.FAILED,
                _duration_ms(started, self._now()),
                0,
                error.code,
            )
            return (
                replace(
                    state,
                    node=InvestigationNode.VERIFY,
                    tool_calls=calls,
                    retrieval_attempts=retrievals,
                    tool_actions=state.tool_actions + (action,),
                    pending_stop_reason=StopReason.TOOL_FAILURE,
                ),
                None,
            )
        action = ToolAction(
            state.node,
            name,
            ToolActionOutcome.SUCCEEDED,
            _duration_ms(started, self._now()),
            _result_count(result),
        )
        advanced = replace(
            state,
            tool_calls=calls,
            retrieval_attempts=retrievals,
            tool_actions=state.tool_actions + (action,),
        )
        reason = self._budget_reason(advanced, include_steps=False)
        if reason is not None:
            return self._terminal(advanced, reason), result
        return advanced, result

    def _after_node(self, state: InvestigationState) -> InvestigationState:
        reason = self._budget_reason(state, include_steps=False)
        return self._terminal(state, reason) if reason is not None else state

    def _budget_reason(
        self, state: InvestigationState, *, include_steps: bool = True
    ) -> StopReason | None:
        if include_steps and state.steps >= state.budget.max_steps:
            return StopReason.MAX_STEPS
        if state.tokens_used >= state.budget.max_tokens:
            return StopReason.TOKEN_BUDGET
        if state.started_at is not None:
            elapsed = (self._now() - state.started_at).total_seconds()
            if elapsed >= state.budget.max_duration_seconds:
                return StopReason.TIMEOUT
        return None

    @staticmethod
    def _tool_budget_reason(
        state: InvestigationState, retrieval: bool
    ) -> StopReason | None:
        if state.tool_calls >= state.budget.max_tool_calls:
            return StopReason.TOOL_BUDGET
        if retrieval and (
            state.retrieval_attempts >= state.budget.max_retrieval_attempts
        ):
            return StopReason.RETRIEVAL_BUDGET
        return None

    def _terminal(
        self, state: InvestigationState, reason: StopReason
    ) -> InvestigationState:
        status = InvestigationStatus.PARTIAL
        if reason is StopReason.SUCCESS:
            status = InvestigationStatus.COMPLETED
        elif reason is StopReason.CANCELLED:
            status = InvestigationStatus.CANCELLED
        elif reason is StopReason.WORKFLOW_FAILURE:
            status = InvestigationStatus.FAILED
        return replace(
            state,
            status=status,
            node=InvestigationNode.DONE,
            stop_reason=reason,
            finished_at=self._now(),
            summary=state.summary or _default_summary(reason),
        )

    def _persist(
        self, current: InvestigationState, state: InvestigationState
    ) -> InvestigationState:
        persisted = replace(
            state,
            revision=current.revision + 1,
            updated_at=self._now(),
        )
        self._store.save(persisted, current.revision)
        return persisted

    def _now(self) -> datetime:
        now = self._clock()
        if now.tzinfo is None:
            raise ValueError("workflow clock requires timezone")
        return now


def _validate_usage(tokens_used: int, cost_microusd: int) -> None:
    if tokens_used < 0 or cost_microusd < 0:
        raise ValueError("usage cannot be negative")


def _duration_ms(started: datetime, finished: datetime) -> int:
    return max(0, int((finished - started).total_seconds() * 1000))


def _result_count(result: object) -> int:
    if isinstance(result, EvidenceSet):
        return len(result.evidence)
    if isinstance(result, tuple):
        return len(result)
    return 1


def _default_summary(reason: StopReason) -> str:
    return {
        StopReason.SUCCESS: "Investigation completed with sufficient evidence.",
        StopReason.INSUFFICIENT_EVIDENCE: (
            "Investigation stopped with insufficient evidence."
        ),
        StopReason.MAX_STEPS: "Investigation stopped at the step budget.",
        StopReason.TIMEOUT: "Investigation stopped at the duration budget.",
        StopReason.TOKEN_BUDGET: "Investigation stopped at the token budget.",
        StopReason.TOOL_BUDGET: "Investigation stopped at the tool-call budget.",
        StopReason.RETRIEVAL_BUDGET: "Investigation stopped at the retrieval budget.",
        StopReason.TOOL_FAILURE: (
            "Investigation completed partially after a tool failure."
        ),
        StopReason.CANCELLED: "Investigation was cancelled.",
        StopReason.WORKFLOW_FAILURE: "Investigation failed safely.",
    }[reason]
