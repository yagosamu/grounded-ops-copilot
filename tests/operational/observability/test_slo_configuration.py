from __future__ import annotations

import importlib.util
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "ops" / "observability" / "slo.yaml"
REPORT_PATH = ROOT / "ops" / "observability" / "reports" / "alert-simulation-v1.json"
VALIDATOR_PATH = ROOT / "scripts" / "validate_slo.py"
REQUIRED_AREAS = {
    "availability",
    "latency",
    "errors",
    "cost",
    "retrieval",
    "abstention",
    "ingestion_freshness",
}
EXPECTED_PAGING_ALERTS = {
    "api-availability-low",
    "retrieval-latency-high",
    "ask-ttft-high",
    "ask-completion-latency-high",
    "ingestion-stale",
}


def _load_validator() -> Any:
    spec = importlib.util.spec_from_file_location("slo_validator", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the SLO validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()
pytestmark = pytest.mark.operational


def test_configuration_covers_operational_questions_with_resolvable_queries() -> None:
    config = validator.load_config(CONFIG_PATH)

    validator.validate_config(config, root=ROOT)
    panels = config["dashboard"]["panels"]
    assert {panel["area"] for panel in panels} == REQUIRED_AREAS
    assert all(panel["question"].endswith("?") for panel in panels)
    assert {metric["status"] for metric in config["metric_catalog"].values()} == {
        "emitted",
        "required_before_pilot",
    }


def test_configuration_rejects_an_unresolvable_dashboard_metric() -> None:
    config = deepcopy(validator.load_config(CONFIG_PATH))
    config["dashboard"]["panels"][0]["query"] = "grounded_ops_missing_total"

    with pytest.raises(ValueError, match="unknown metrics"):
        validator.validate_config(config, root=ROOT)


def test_every_paging_alert_is_actionable_and_fires_only_for_failure() -> None:
    config = validator.load_config(CONFIG_PATH)
    results = validator.simulate_alerts(config)
    paging_results = {
        item.alert_id: item for item in results if item.severity == "page"
    }

    assert set(paging_results) == EXPECTED_PAGING_ALERTS
    for result in paging_results.values():
        assert result.healthy_fired is False
        assert result.failure_fired is True
        assert result.duration_minutes >= 5
        assert result.runbook_exists is True


def test_alerts_use_only_page_or_ticket_and_have_valid_runbooks() -> None:
    config = validator.load_config(CONFIG_PATH)

    for alert in config["alerts"]:
        assert alert["severity"] in {"page", "ticket"}
        assert alert["symptom"]
        runbook = ROOT / alert["runbook"]
        assert runbook.is_file()
        content = runbook.read_text(encoding="utf-8")
        assert f"## {alert['id']}" in content
        assert "**Means:**" in content
        assert "**First check:**" in content
        assert "**Escalate to:**" in content


def test_committed_simulation_report_is_bound_to_config_and_all_pages_pass() -> None:
    report = json.loads(REPORT_PATH.read_text(encoding="utf-8"))

    validator.validate_report(report, config_path=CONFIG_PATH, root=ROOT)
    assert report["schema_version"] == "alert-simulation.v1"
    assert report["paging_alerts_tested"] == len(EXPECTED_PAGING_ALERTS)
    assert report["paging_alerts_passed"] == len(EXPECTED_PAGING_ALERTS)
    assert report["result"] == "pass"
    assert report["evidence"]["load_profile"]["sha256"]
    assert report["evidence"]["degraded_modes"]["test_nodes"]
