"""AGT-01 investigation transitions stop at explicit, persisted boundaries."""

from datetime import UTC, datetime, timedelta

import pytest

from domain.answering import Question
from modules.investigation.tools import (
    CompareVersionsRequest,
    IncidentRecord,
    IncidentSearchRequest,
    InvestigationToolError,
    RetrieveRequest,
    ToolErrorCode,
    VersionChange,
    VersionComparison,
)
from modules.investigation.workflow import (
    InMemoryInvestigationStore,
    InvestigationBudget,
    InvestigationNode,
    InvestigationPlan,
    InvestigationStatus,
    InvestigationTask,
    InvestigationWorkflow,
    PlanResult,
    ReportResult,
    StopReason,
    VerificationResult,
)
from modules.policy.authorizer import AuthorizationReason, Principal
from modules.retrieval.retriever import Evidence, EvidenceSet

pytestmark = pytest.mark.unit

STAMP = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
PRINCIPAL = Principal("alice", "alpha", ("engineer",), ())
TASK = InvestigationTask(
    "investigation-1", Question("question-1", "Why did it fail?"), PRINCIPAL
)


class MutableClock:
    def __init__(self) -> None:
        self.now = STAMP

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


class RecordingStore(InMemoryInvestigationStore):
    def __init__(self) -> None:
        super().__init__()
        self.nodes: list[InvestigationNode] = []

    def save(self, state, expected_revision: int) -> None:
        super().save(state, expected_revision)
        self.nodes.append(state.node)


class FakePlanner:
    def __init__(
        self,
        plan: InvestigationPlan,
        *,
        tokens: int = 10,
        cost_microusd: int = 100,
        clock: MutableClock | None = None,
        elapsed: float = 0,
    ) -> None:
        self.result = PlanResult(plan, tokens, cost_microusd)
        self.clock = clock
        self.elapsed = elapsed

    def plan(self, task: InvestigationTask) -> PlanResult:
        if self.clock is not None:
            self.clock.advance(self.elapsed)
        return self.result


class FakeTools:
    def __init__(self, *, fail_retrieval: bool = False) -> None:
        self.fail_retrieval = fail_retrieval

    def retrieve(self, principal, request):
        if self.fail_retrieval:
            raise InvestigationToolError(ToolErrorCode.BACKEND_UNAVAILABLE)
        return EvidenceSet((evidence(),), "bm25", 1, 3)

    def compare_versions(self, principal, request):
        return VersionComparison(
            request.document_id,
            request.left_version_id,
            request.right_version_id,
            False,
            (VersionChange("content_hash", "a", "b"),),
        )

    def search_incidents(self, principal, request):
        return (
            IncidentRecord(
                "incident-1",
                principal.tenant_id,
                "document-1",
                "version-2",
                "Collector outage",
                "Recovered after rollback",
                STAMP,
            ),
        )


class FakeVerifier:
    def __init__(
        self,
        sufficient: bool,
        unresolved: tuple[str, ...] = (),
        *,
        tokens: int = 5,
        cost_microusd: int = 50,
    ) -> None:
        self.result = VerificationResult(sufficient, unresolved, tokens, cost_microusd)

    def verify(self, state):
        return self.result


class FakeReporter:
    def report(self, state):
        return ReportResult("Grounded incident report", 5, 50)


def evidence() -> Evidence:
    return Evidence(
        "chunk-1",
        "alpha",
        "source-1",
        "document-1",
        "version-2",
        0,
        "collector failed after deployment",
        (0, 33),
        "a" * 64,
        "parser-v1",
        "chunker-v1",
        STAMP,
        True,
        4.2,
        AuthorizationReason.ROLE,
    )


def full_plan() -> InvestigationPlan:
    return InvestigationPlan(
        retrievals=(RetrieveRequest("collector failure", 3),),
        comparisons=(CompareVersionsRequest("document-1", "version-1", "version-2"),),
        incidents=(IncidentSearchRequest("collector outage", 2),),
    )


def workflow(
    plan: InvestigationPlan | None = None,
    *,
    tools: FakeTools | None = None,
    verifier: FakeVerifier | None = None,
    planner: FakePlanner | None = None,
    store: InMemoryInvestigationStore | None = None,
    clock: MutableClock | None = None,
) -> InvestigationWorkflow:
    actual_plan = plan or full_plan()
    return InvestigationWorkflow(
        store or InMemoryInvestigationStore(),
        planner or FakePlanner(actual_plan),
        tools or FakeTools(),
        verifier or FakeVerifier(True),
        FakeReporter(),
        clock=clock or MutableClock(),
    )


def test_success_runs_every_node_and_returns_a_complete_auditable_report() -> None:
    store = RecordingStore()
    subject = workflow(store=store)
    subject.start(TASK, InvestigationBudget(8, 60, 100, 8, 4))

    completed = subject.run(TASK.id)
    report = subject.report(TASK.id)

    assert completed.status is InvestigationStatus.COMPLETED
    assert completed.node is InvestigationNode.DONE
    assert completed.stop_reason is StopReason.SUCCESS
    assert completed.steps == 5
    assert completed.tokens_used == 20
    assert completed.cost_microusd == 200
    assert completed.tool_calls == 3
    assert completed.retrieval_attempts == 2
    assert store.nodes == [
        InvestigationNode.RETRIEVE,
        InvestigationNode.COMPARE,
        InvestigationNode.VERIFY,
        InvestigationNode.REPORT,
        InvestigationNode.DONE,
    ]
    assert tuple(action.tool for action in report.tool_actions) == (
        "retrieve",
        "search_incidents",
        "compare_versions",
    )
    assert report.evidence == (evidence(),)
    assert report.unresolved_questions == ()
    assert report.stop_reason is StopReason.SUCCESS
    assert report.tokens_used == 20
    assert report.cost_microusd == 200


def test_insufficient_evidence_returns_partial_report_with_unresolved_questions() -> (
    None
):
    empty_plan = InvestigationPlan(retrievals=(RetrieveRequest("missing"),))
    empty_tools = FakeTools()
    empty_tools.retrieve = lambda principal, request: EvidenceSet((), "bm25", 0, 1)
    subject = workflow(
        empty_plan,
        tools=empty_tools,
        verifier=FakeVerifier(False, ("Which deployment changed?",)),
    )
    subject.start(TASK, InvestigationBudget(8, 60, 100, 8, 4))

    partial = subject.run(TASK.id)
    report = subject.report(TASK.id)

    assert partial.status is InvestigationStatus.PARTIAL
    assert partial.stop_reason is StopReason.INSUFFICIENT_EVIDENCE
    assert report.evidence == ()
    assert report.unresolved_questions == ("Which deployment changed?",)


def test_max_steps_stops_before_the_next_node_and_preserves_state() -> None:
    subject = workflow()
    subject.start(TASK, InvestigationBudget(1, 60, 100, 8, 4))

    partial = subject.run(TASK.id)

    assert partial.status is InvestigationStatus.PARTIAL
    assert partial.stop_reason is StopReason.MAX_STEPS
    assert partial.steps == 1
    assert partial.plan == full_plan()
    assert subject.report(TASK.id).tool_actions == ()


def test_duration_budget_stops_after_a_slow_node() -> None:
    clock = MutableClock()
    subject = workflow(
        planner=FakePlanner(full_plan(), clock=clock, elapsed=2), clock=clock
    )
    subject.start(TASK, InvestigationBudget(8, 1, 100, 8, 4))

    partial = subject.run(TASK.id)

    assert partial.status is InvestigationStatus.PARTIAL
    assert partial.stop_reason is StopReason.TIMEOUT
    assert subject.report(TASK.id).duration_ms == 2000


def test_token_budget_stops_without_executing_tools() -> None:
    subject = workflow(planner=FakePlanner(full_plan(), tokens=11))
    subject.start(TASK, InvestigationBudget(8, 60, 10, 8, 4))

    partial = subject.run(TASK.id)

    assert partial.status is InvestigationStatus.PARTIAL
    assert partial.stop_reason is StopReason.TOKEN_BUDGET
    assert partial.tokens_used == 11
    assert partial.tool_calls == 0


@pytest.mark.parametrize(
    ("budget", "reason", "expected_calls", "expected_retrievals"),
    [
        (InvestigationBudget(8, 60, 100, 1, 4), StopReason.TOOL_BUDGET, 1, 1),
        (
            InvestigationBudget(8, 60, 100, 8, 1),
            StopReason.RETRIEVAL_BUDGET,
            1,
            1,
        ),
    ],
)
def test_tool_and_retrieval_budgets_stop_mid_node_with_partial_evidence(
    budget: InvestigationBudget,
    reason: StopReason,
    expected_calls: int,
    expected_retrievals: int,
) -> None:
    subject = workflow()
    subject.start(TASK, budget)

    partial = subject.run(TASK.id)

    assert partial.status is InvestigationStatus.PARTIAL
    assert partial.stop_reason is reason
    assert partial.tool_calls == expected_calls
    assert partial.retrieval_attempts == expected_retrievals
    assert subject.report(TASK.id).evidence == (evidence(),)


def test_tool_failure_is_redacted_and_returns_a_partial_report() -> None:
    subject = workflow(
        tools=FakeTools(fail_retrieval=True),
        verifier=FakeVerifier(False, ("Retrieval unavailable",)),
    )
    subject.start(TASK, InvestigationBudget(8, 60, 100, 8, 4))

    partial = subject.run(TASK.id)
    report = subject.report(TASK.id)

    assert partial.status is InvestigationStatus.PARTIAL
    assert partial.stop_reason is StopReason.TOOL_FAILURE
    assert report.tool_actions[0].outcome == "failed"
    assert report.tool_actions[0].error_code is ToolErrorCode.BACKEND_UNAVAILABLE
    assert "private" not in repr(report)


def test_cancellation_is_terminal_and_keeps_a_partial_report() -> None:
    subject = workflow()
    subject.start(TASK, InvestigationBudget(8, 60, 100, 8, 4))

    cancelled = subject.cancel(TASK.id)

    assert cancelled.status is InvestigationStatus.CANCELLED
    assert cancelled.stop_reason is StopReason.CANCELLED
    assert cancelled.node is InvestigationNode.DONE
    assert subject.report(TASK.id).stop_reason is StopReason.CANCELLED


def test_resume_continues_from_the_last_persisted_node_without_replanning() -> None:
    store = InMemoryInvestigationStore()
    first = workflow(store=store)
    first.start(TASK, InvestigationBudget(8, 60, 100, 8, 4))
    planned = first.advance(TASK.id)

    assert planned.node is InvestigationNode.RETRIEVE
    assert planned.steps == 1

    resumed = workflow(store=store).run(TASK.id)

    assert resumed.status is InvestigationStatus.COMPLETED
    assert resumed.steps == 5
    assert resumed.plan == full_plan()
    assert resumed.revision > planned.revision
