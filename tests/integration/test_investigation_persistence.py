"""A worker can resume an investigation from a committed PostgreSQL checkpoint."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, text

from adapters.postgres.investigation_store import PostgresInvestigationStore
from domain.answering import Question
from modules.investigation.workflow import (
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
from modules.policy.authorizer import Principal

pytestmark = [pytest.mark.integration, pytest.mark.agentic]
STAMP = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class Planner:
    def plan(self, task: InvestigationTask) -> PlanResult:
        return PlanResult(InvestigationPlan(), 3, 10)


class UnusedTools:
    def retrieve(self, principal, request):
        raise AssertionError("empty plan must not retrieve")

    def compare_versions(self, principal, request):
        raise AssertionError("empty plan must not compare")

    def search_incidents(self, principal, request):
        raise AssertionError("empty plan must not search incidents")


class Verifier:
    def verify(self, state):
        return VerificationResult(True, (), 2, 5)


class Reporter:
    def report(self, state):
        return ReportResult("Durably resumed report", 2, 5)


def workflow(store: PostgresInvestigationStore) -> InvestigationWorkflow:
    return InvestigationWorkflow(
        store,
        Planner(),
        UnusedTools(),
        Verifier(),
        Reporter(),
        clock=lambda: STAMP,
    )


def test_committed_checkpoint_resumes_in_a_new_worker_transaction(
    database: Engine,
) -> None:
    task = InvestigationTask(
        "investigation-resume",
        Question("question-resume", "Compare the two incident timelines"),
        Principal("alice", "alpha", ("engineer",), ()),
    )
    budget = InvestigationBudget(8, 60, 100, 8, 4)

    with database.begin() as connection:
        first_worker = workflow(PostgresInvestigationStore(connection))
        first_worker.start(task, budget)
        checkpoint = first_worker.advance(task.id)

    assert checkpoint.node is InvestigationNode.RETRIEVE
    assert checkpoint.revision == 1

    with database.begin() as connection:
        second_worker = workflow(PostgresInvestigationStore(connection))
        completed = second_worker.run(task.id)
        report = second_worker.report(task.id)
        row = (
            connection.execute(
                text("SELECT status,node,revision,state_payload FROM investigations")
            )
            .mappings()
            .one()
        )

    assert completed.status is InvestigationStatus.COMPLETED
    assert completed.stop_reason is StopReason.SUCCESS
    assert completed.steps == 5
    assert report.summary == "Durably resumed report"
    assert report.tokens_used == 7
    assert row["status"] == "completed"
    assert row["node"] == "done"
    assert row["revision"] == completed.revision
    assert row["state_payload"]["task"]["principal"]["tenant_id"] == "alpha"
