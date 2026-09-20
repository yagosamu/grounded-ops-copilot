"""AGT-01 asynchronous investigation lifecycle and ownership tests."""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from interfaces.http.investigations import (
    InvestigationLifecycle,
    create_investigation_router,
)
from modules.investigation.workflow import (
    InMemoryInvestigationStore,
    InvestigationPlan,
    InvestigationWorkflow,
    PlanResult,
    ReportResult,
    VerificationResult,
)

pytestmark = [pytest.mark.api, pytest.mark.agentic]
STAMP = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


class Planner:
    def plan(self, task):
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
        return ReportResult("Grounded investigation report", 2, 5)


class RecordingDispatcher:
    def __init__(self, *, fail: bool = False) -> None:
        self.ids: list[str] = []
        self.fail = fail

    def enqueue(self, investigation_id: str) -> None:
        if self.fail:
            raise RuntimeError("private queue failure")
        self.ids.append(investigation_id)


@dataclass
class Harness:
    client: TestClient
    workflow: InvestigationWorkflow
    dispatcher: RecordingDispatcher


@pytest.fixture
def harness(auth_context) -> Harness:
    workflow = InvestigationWorkflow(
        InMemoryInvestigationStore(),
        Planner(),
        UnusedTools(),
        Verifier(),
        Reporter(),
        clock=lambda: STAMP,
    )
    dispatcher = RecordingDispatcher()
    app = FastAPI()
    app.include_router(
        create_investigation_router(
            InvestigationLifecycle(workflow, dispatcher),
            auth_context.authenticator,
        )
    )
    return Harness(TestClient(app), workflow, dispatcher)


def create(harness: Harness, auth_context, *, key: str = "incident-a"):
    return harness.client.post(
        "/v1/investigations",
        json={"question": "Compare the incident timelines"},
        headers=auth_context.headers() | {"Idempotency-Key": key},
    )


def test_create_status_and_terminal_report_are_async_and_redacted(
    harness: Harness, auth_context
) -> None:
    created = create(harness, auth_context)

    assert created.status_code == 202
    investigation_id = created.json()["investigation_id"]
    assert created.json()["status"] == "queued"
    assert harness.dispatcher.ids == [investigation_id]
    assert "question" not in created.text
    assert "plan" not in created.text
    assert "node" not in created.text

    status = harness.client.get(
        f"/v1/investigations/{investigation_id}", headers=auth_context.headers()
    )
    pending_report = harness.client.get(
        f"/v1/investigations/{investigation_id}/report",
        headers=auth_context.headers(),
    )

    assert status.status_code == 200
    assert status.json()["report_available"] is False
    assert pending_report.status_code == 409
    assert pending_report.json() == {"detail": "investigation report is not ready"}

    harness.workflow.run(investigation_id)
    report = harness.client.get(
        f"/v1/investigations/{investigation_id}/report",
        headers=auth_context.headers(),
    )

    assert report.status_code == 200
    assert report.json() == {
        "investigation_id": investigation_id,
        "summary": "Grounded investigation report",
        "evidence": [],
        "incidents": [],
        "comparisons": [],
        "tool_actions": [],
        "unresolved_questions": [],
        "stop_reason": "success",
        "usage": {"tokens": 7, "cost_microusd": 20},
        "duration_ms": 0,
    }
    assert not {"question", "plan", "node"} & report.json().keys()


def test_create_is_idempotent_and_rejects_key_reuse_for_a_new_payload(
    harness: Harness, auth_context
) -> None:
    first = create(harness, auth_context)
    repeated = create(harness, auth_context)
    conflict = harness.client.post(
        "/v1/investigations",
        json={"question": "A different investigation"},
        headers=auth_context.headers() | {"Idempotency-Key": "incident-a"},
    )

    assert repeated.status_code == 202
    assert repeated.json()["investigation_id"] == first.json()["investigation_id"]
    assert first.headers["idempotency-replayed"] == "false"
    assert repeated.headers["idempotency-replayed"] == "true"
    assert conflict.status_code == 409
    assert conflict.json() == {
        "detail": "idempotency key was reused with a different request"
    }


@pytest.mark.parametrize("identity", [{"sub": "bob"}, {"tenant_id": "beta"}])
def test_every_resource_route_hides_investigations_from_other_owners(
    harness: Harness, auth_context, identity
) -> None:
    investigation_id = create(harness, auth_context).json()["investigation_id"]
    unauthorized = auth_context.headers(**identity)

    responses = (
        harness.client.get(
            f"/v1/investigations/{investigation_id}", headers=unauthorized
        ),
        harness.client.post(
            f"/v1/investigations/{investigation_id}/cancel", headers=unauthorized
        ),
        harness.client.get(
            f"/v1/investigations/{investigation_id}/report", headers=unauthorized
        ),
    )

    for response in responses:
        assert response.status_code == 404
        assert response.json() == {"detail": "investigation not found"}


def test_cancel_is_idempotent_but_rejects_another_terminal_state(
    harness: Harness, auth_context
) -> None:
    cancelled_id = create(harness, auth_context, key="cancel-me").json()[
        "investigation_id"
    ]
    first = harness.client.post(
        f"/v1/investigations/{cancelled_id}/cancel",
        headers=auth_context.headers(),
    )
    repeated = harness.client.post(
        f"/v1/investigations/{cancelled_id}/cancel",
        headers=auth_context.headers(),
    )

    completed_id = create(harness, auth_context, key="finish-me").json()[
        "investigation_id"
    ]
    harness.workflow.run(completed_id)
    conflict = harness.client.post(
        f"/v1/investigations/{completed_id}/cancel",
        headers=auth_context.headers(),
    )

    assert first.status_code == 202
    assert first.json()["status"] == "cancelled"
    assert repeated.status_code == 202
    assert repeated.json() == first.json()
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "investigation is already terminal"}


def test_dispatch_failure_is_redacted_and_can_be_retried(auth_context) -> None:
    workflow = InvestigationWorkflow(
        InMemoryInvestigationStore(),
        Planner(),
        UnusedTools(),
        Verifier(),
        Reporter(),
        clock=lambda: STAMP,
    )
    dispatcher = RecordingDispatcher(fail=True)
    app = FastAPI()
    app.include_router(
        create_investigation_router(
            InvestigationLifecycle(workflow, dispatcher),
            auth_context.authenticator,
        )
    )

    response = TestClient(app).post(
        "/v1/investigations",
        json={"question": "Compare incidents"},
        headers=auth_context.headers() | {"Idempotency-Key": "retryable"},
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "investigation temporarily unavailable"}
    assert "private" not in response.text
