"""Managed broker settings keep production queues scoped and keyless."""

import pytest
from celery import Celery

from grounded_ops.worker import _configure_broker

pytestmark = pytest.mark.unit


def test_local_worker_keeps_redis_broker() -> None:
    app = Celery("broker-local")

    _configure_broker(app, {"REDIS_URL": "redis://redis:6379/0"})

    assert app.conf.broker_url == "redis://redis:6379/0"
    assert app.conf.task_default_queue == "celery"
    assert app.conf.broker_transport_options == {"visibility_timeout": 180}


def test_production_worker_uses_predefined_sqs_queue_and_task_identity() -> None:
    app = Celery("broker-production")
    queue_url = "https://sqs.us-east-1.amazonaws.com/123456789012/grounded-ops-jobs"

    _configure_broker(
        app,
        {
            "AWS_REGION": "us-east-1",
            "SQS_QUEUE_NAME": "grounded-ops-jobs",
            "SQS_QUEUE_URL": queue_url,
        },
    )

    assert app.conf.broker_url == "sqs://"
    assert app.conf.task_default_queue == "grounded-ops-jobs"
    assert app.conf.broker_transport_options == {
        "region": "us-east-1",
        "predefined_queues": {"grounded-ops-jobs": {"url": queue_url}},
        "wait_time_seconds": 10,
    }


@pytest.mark.parametrize(
    "environment",
    [
        {"SQS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/q"},
        {"SQS_QUEUE_NAME": "q", "AWS_REGION": "us-east-1"},
    ],
)
def test_partial_sqs_configuration_fails_closed(environment: dict[str, str]) -> None:
    with pytest.raises(ValueError, match="SQS broker requires"):
        _configure_broker(Celery("broker-incomplete"), environment)
