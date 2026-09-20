"""AGT-02 production routing remains disabled until benchmark promotion."""

from pathlib import Path

import pytest

from domain.answering import Question
from modules.routing.config import load_agent_routing_config
from modules.routing.query_router import QueryRouter, Route, RouteReason

pytestmark = pytest.mark.unit
ROOT = Path(__file__).parents[3]


def test_production_config_is_disabled_and_binds_benchmark_and_budgets() -> None:
    config = load_agent_routing_config(ROOT / "config/agentic.yaml")

    assert config.investigate_enabled is False
    assert config.enabled_task_classes == ()
    assert config.max_steps == 12
    assert config.max_duration_seconds == 120
    assert config.max_tokens == 20_000
    assert config.max_tool_calls == 8
    assert config.max_retrieval_attempts == 5
    assert config.minimum_success_gain == 0.10
    assert config.maximum_cost_ratio == 2.5
    assert config.benchmark_report.endswith("evals/agentic/reports/agentic-v1.json")

    decision = QueryRouter(investigate_enabled=config.investigate_enabled).route(
        Question("q-config", "Compare the incident timeline.")
    )
    assert decision.route is Route.ASK
    assert decision.reason is RouteReason.MULTI_HOP
    assert decision.fallback_used is True


def test_routing_config_rejects_tampered_benchmark_report(tmp_path: Path) -> None:
    source = ROOT / "config/agentic.yaml"
    config_path = tmp_path / "config/agentic.yaml"
    config_path.parent.mkdir(parents=True)
    report_path = tmp_path / "evals/agentic/reports/agentic-v1.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(
        (ROOT / "evals/agentic/reports/agentic-v1.json").read_bytes() + b"tampered"
    )
    config_path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(ValueError, match="benchmark report hash mismatch"):
        load_agent_routing_config(config_path)


def test_disabled_config_cannot_list_enabled_task_classes(tmp_path: Path) -> None:
    source = (ROOT / "config/agentic.yaml").read_text(encoding="utf-8")
    config_path = tmp_path / "config/agentic.yaml"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        source.replace("enabled_task_classes: []", "enabled_task_classes: [multi_hop]"),
        encoding="utf-8",
    )
    report_path = tmp_path / "evals/agentic/reports/agentic-v1.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_bytes(
        (ROOT / "evals/agentic/reports/agentic-v1.json").read_bytes()
    )

    with pytest.raises(ValueError, match="disabled investigation"):
        load_agent_routing_config(config_path)
