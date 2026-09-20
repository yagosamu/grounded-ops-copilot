"""Validated production routing configuration for the agentic path."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

import yaml


@dataclass(frozen=True)
class AgentRoutingConfig:
    version: int
    investigate_enabled: bool
    enabled_task_classes: tuple[str, ...]
    max_steps: int
    max_duration_seconds: float
    max_tokens: int
    max_tool_calls: int
    max_retrieval_attempts: int
    benchmark_report: str
    benchmark_report_sha256: str
    minimum_success_gain: float
    maximum_cost_ratio: float


def load_agent_routing_config(path: Path) -> AgentRoutingConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    config = AgentRoutingConfig(
        int(raw["version"]),
        bool(raw["investigate"]["enabled"]),
        tuple(str(value) for value in raw["investigate"]["enabled_task_classes"]),
        int(raw["budgets"]["max_steps"]),
        float(raw["budgets"]["max_duration_seconds"]),
        int(raw["budgets"]["max_tokens"]),
        int(raw["budgets"]["max_tool_calls"]),
        int(raw["budgets"]["max_retrieval_attempts"]),
        str(raw["benchmark"]["report"]),
        str(raw["benchmark"]["report_sha256"]),
        float(raw["benchmark"]["minimum_success_gain"]),
        float(raw["benchmark"]["maximum_cost_ratio"]),
    )
    _validate(config, path)
    return config


def _validate(config: AgentRoutingConfig, path: Path) -> None:
    if config.version != 1:
        raise ValueError("unsupported agent routing config version")
    if any(value != "multi_hop" for value in config.enabled_task_classes):
        raise ValueError("unsupported enabled agent task class")
    if not config.investigate_enabled and config.enabled_task_classes:
        raise ValueError("disabled investigation cannot enable task classes")
    if any(
        value <= 0
        for value in (
            config.max_steps,
            config.max_duration_seconds,
            config.max_tokens,
            config.max_tool_calls,
            config.max_retrieval_attempts,
        )
    ):
        raise ValueError("investigation budgets must be positive")
    if config.minimum_success_gain < 0 or config.maximum_cost_ratio <= 0:
        raise ValueError("invalid agent promotion thresholds")
    if len(config.benchmark_report_sha256) != 64:
        raise ValueError("invalid benchmark report hash")
    report = (path.parent.parent / config.benchmark_report).resolve()
    if not report.is_file():
        raise ValueError("benchmark report is missing")
    if sha256(report.read_bytes()).hexdigest() != config.benchmark_report_sha256:
        raise ValueError("benchmark report hash mismatch")
