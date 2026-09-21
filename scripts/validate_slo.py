"""Validate the observability contract and simulate every configured alert."""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

import yaml

REQUIRED_AREAS = {
    "availability",
    "latency",
    "errors",
    "cost",
    "retrieval",
    "abstention",
    "ingestion_freshness",
}
FORBIDDEN_LABELS = {
    "correlation_id",
    "document_id",
    "error_message",
    "principal_id",
    "question",
    "request_id",
    "tenant_id",
    "user_id",
}
METRIC_PATTERN = re.compile(r"\bgrounded_ops_[a-z0-9_]+\b")
DURATION_PATTERN = re.compile(r"^(?P<amount>[1-9][0-9]*)(?P<unit>[mh])$")
COMPARATORS = {
    "gt": lambda value, threshold: value > threshold,
    "gte": lambda value, threshold: value >= threshold,
    "lt": lambda value, threshold: value < threshold,
    "lte": lambda value, threshold: value <= threshold,
}


@dataclass(frozen=True)
class SimulationResult:
    alert_id: str
    severity: str
    healthy_fired: bool
    failure_fired: bool
    duration_minutes: int
    runbook_exists: bool


def load_config(path: Path) -> dict[str, Any]:
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("SLO configuration must be a mapping")
    return payload


def validate_config(config: Mapping[str, Any], *, root: Path) -> None:
    required = {
        "version",
        "owner",
        "evaluation_interval",
        "metric_catalog",
        "slos",
        "dashboard",
        "alerts",
        "evidence",
    }
    if set(config) != required or config["version"] != 1:
        raise ValueError("invalid SLO configuration envelope")
    catalog = _mapping(config["metric_catalog"], "metric catalog")
    _validate_metrics(catalog)
    slos = _mapping(config["slos"], "SLOs")
    if not slos:
        raise ValueError("at least one SLO is required")
    for slo in slos.values():
        item = _mapping(slo, "SLO")
        if not item.get("window"):
            raise ValueError("SLO window is required")
        _validate_query(str(item.get("query", "")), catalog)
    dashboard = _mapping(config["dashboard"], "dashboard")
    panels = _sequence(dashboard.get("panels"), "dashboard panels")
    areas = {str(_mapping(panel, "dashboard panel").get("area")) for panel in panels}
    if areas != REQUIRED_AREAS:
        raise ValueError("dashboard must cover every required operational area")
    for panel in panels:
        item = _mapping(panel, "dashboard panel")
        if not str(item.get("question", "")).endswith("?"):
            raise ValueError("dashboard panel must answer an operational question")
        _validate_query(str(item.get("query", "")), catalog)
    alerts = _sequence(config["alerts"], "alerts")
    if not alerts:
        raise ValueError("at least one alert is required")
    identifiers: set[str] = set()
    for alert in alerts:
        item = _mapping(alert, "alert")
        identifier = str(item.get("id", ""))
        if not identifier or identifier in identifiers:
            raise ValueError("alert identifiers must be present and unique")
        identifiers.add(identifier)
        _validate_alert(item, catalog=catalog, root=root)
    _validate_evidence(_mapping(config["evidence"], "evidence"), root=root)


def _validate_metrics(catalog: Mapping[str, Any]) -> None:
    if not catalog:
        raise ValueError("metric catalog cannot be empty")
    for name, raw_metric in catalog.items():
        if not METRIC_PATTERN.fullmatch(str(name)):
            raise ValueError("invalid metric name")
        metric = _mapping(raw_metric, "metric")
        if metric.get("status") not in {"emitted", "required_before_pilot"}:
            raise ValueError("metric status must disclose runtime availability")
        labels = set(_sequence(metric.get("labels"), "metric labels"))
        if labels & FORBIDDEN_LABELS:
            raise ValueError("metric contains a high-cardinality or sensitive label")
        if not metric.get("producer"):
            raise ValueError("metric producer is required")


def _validate_alert(
    alert: Mapping[str, Any], *, catalog: Mapping[str, Any], root: Path
) -> None:
    if alert.get("severity") not in {"page", "ticket"}:
        raise ValueError("alert severity must be page or ticket")
    if not str(alert.get("symptom", "")).strip():
        raise ValueError("alert symptom is required")
    if alert.get("comparator") not in COMPARATORS:
        raise ValueError("invalid alert comparator")
    if not isinstance(alert.get("threshold"), (int, float)):
        raise ValueError("alert threshold must be numeric")
    _duration_minutes(str(alert.get("for", "")))
    _validate_query(str(alert.get("query", "")), catalog)
    simulation = _mapping(alert.get("simulation"), "alert simulation")
    if not isinstance(simulation.get("healthy"), (int, float)) or not isinstance(
        simulation.get("failure"), (int, float)
    ):
        raise ValueError("alert simulation values must be numeric")
    runbook = root / str(alert.get("runbook", ""))
    if not runbook.is_file():
        raise ValueError("alert runbook does not exist")
    if f"## {alert['id']}" not in runbook.read_text(encoding="utf-8"):
        raise ValueError("alert runbook section does not exist")


def _validate_query(query: str, catalog: Mapping[str, Any]) -> None:
    metrics = set(METRIC_PATTERN.findall(query))
    if not query.strip() or not metrics:
        raise ValueError("query must reference at least one GroundedOps metric")
    unresolved = metrics - set(catalog)
    if unresolved:
        raise ValueError(f"query references unknown metrics: {sorted(unresolved)}")


def _validate_evidence(evidence: Mapping[str, Any], *, root: Path) -> None:
    load_profile = root / str(evidence.get("load_profile", ""))
    if not load_profile.is_file():
        raise ValueError("load profile evidence does not exist")
    degraded = _mapping(evidence.get("degraded_modes"), "degraded-mode evidence")
    nodes = _sequence(degraded.get("test_nodes"), "degraded-mode test nodes")
    if not nodes:
        raise ValueError("degraded-mode evidence cannot be empty")
    for node in nodes:
        path_value, separator, test_name = str(node).partition("::")
        path = root / path_value
        if not separator or not path.is_file():
            raise ValueError("invalid degraded-mode test node")
        if f"def {test_name}(" not in path.read_text(encoding="utf-8"):
            raise ValueError("degraded-mode test node does not resolve")


def simulate_alerts(
    config: Mapping[str, Any], *, root: Path | None = None
) -> tuple[SimulationResult, ...]:
    base = root or Path.cwd()
    results: list[SimulationResult] = []
    for raw_alert in _sequence(config.get("alerts"), "alerts"):
        alert = _mapping(raw_alert, "alert")
        comparator = COMPARATORS[str(alert["comparator"])]
        threshold = float(alert["threshold"])
        simulation = _mapping(alert["simulation"], "alert simulation")
        results.append(
            SimulationResult(
                alert_id=str(alert["id"]),
                severity=str(alert["severity"]),
                healthy_fired=comparator(float(simulation["healthy"]), threshold),
                failure_fired=comparator(float(simulation["failure"]), threshold),
                duration_minutes=_duration_minutes(str(alert["for"])),
                runbook_exists=(base / str(alert["runbook"])).is_file(),
            )
        )
    return tuple(results)


def build_report(*, config_path: Path, root: Path) -> dict[str, Any]:
    config = load_config(config_path)
    validate_config(config, root=root)
    simulations = simulate_alerts(config, root=root)
    paging = tuple(item for item in simulations if item.severity == "page")
    paging_passed = sum(
        not item.healthy_fired and item.failure_fired and item.runbook_exists
        for item in paging
    )
    evidence = _mapping(config["evidence"], "evidence")
    load_profile = root / str(evidence["load_profile"])
    degraded = _mapping(evidence["degraded_modes"], "degraded-mode evidence")
    return {
        "schema_version": "alert-simulation.v1",
        "generated_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "config": {
            "path": config_path.relative_to(root).as_posix(),
            "sha256": _file_hash(config_path),
        },
        "metric_readiness": {
            status: sum(
                metric["status"] == status
                for metric in config["metric_catalog"].values()
            )
            for status in ("emitted", "required_before_pilot")
        },
        "paging_alerts_tested": len(paging),
        "paging_alerts_passed": paging_passed,
        "result": "pass" if paging_passed == len(paging) else "fail",
        "simulations": [asdict(item) for item in simulations],
        "evidence": {
            "load_profile": {
                "path": load_profile.relative_to(root).as_posix(),
                "sha256": _file_hash(load_profile),
            },
            "degraded_modes": {
                "test_nodes": list(degraded["test_nodes"]),
            },
        },
    }


def validate_report(
    report: Mapping[str, Any], *, config_path: Path, root: Path
) -> None:
    if report.get("schema_version") != "alert-simulation.v1":
        raise ValueError("invalid alert simulation report version")
    if _mapping(report.get("config"), "report config").get("sha256") != _file_hash(
        config_path
    ):
        raise ValueError("report is not bound to the current SLO configuration")
    expected = build_report(config_path=config_path, root=root)
    stable_fields = (
        "metric_readiness",
        "paging_alerts_tested",
        "paging_alerts_passed",
        "result",
        "simulations",
        "evidence",
    )
    if any(report.get(field) != expected[field] for field in stable_fields):
        raise ValueError("alert simulation report does not match current configuration")


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be a mapping")
    return value


def _sequence(value: Any, name: str) -> Sequence[Any]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise ValueError(f"{name} must be a sequence")
    return value


def _duration_minutes(value: str) -> int:
    match = DURATION_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("alert duration must use minutes or hours")
    amount = int(match.group("amount"))
    return amount * (60 if match.group("unit") == "h" else 1)


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    root = args.root.resolve()
    config_path = args.config.resolve()
    report = build_report(config_path=config_path, root=root)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
