"""Apply the project's high/critical threshold to SARIF security findings."""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def blocking_findings(report: Mapping[str, Any], *, minimum_score: float) -> list[str]:
    """Return unique SARIF rule IDs at or above the blocking CVSS score."""
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise ValueError("SARIF report must contain at least one run")
    blocked: set[str] = set()
    for raw_run in runs:
        run = _mapping(raw_run, "SARIF run")
        tool = _mapping(run.get("tool"), "SARIF tool")
        driver = _mapping(tool.get("driver"), "SARIF driver")
        rules = driver.get("rules", [])
        if not isinstance(rules, list):
            raise ValueError("SARIF rules must be a list")
        for raw_rule in rules:
            rule = _mapping(raw_rule, "SARIF rule")
            properties = _mapping(rule.get("properties", {}), "SARIF properties")
            raw_score = properties.get("security-severity")
            if raw_score is None:
                continue
            try:
                score = float(raw_score)
            except (TypeError, ValueError) as error:
                raise ValueError("SARIF security severity must be numeric") from error
            rule_id = rule.get("id")
            if not isinstance(rule_id, str) or not rule_id:
                raise ValueError("SARIF security rule must have an id")
            if score >= minimum_score:
                blocked.add(rule_id)
    return sorted(blocked)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    parser.add_argument("--minimum-score", type=float, default=7.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Return nonzero when the report contains a blocking vulnerability."""
    args = _parser().parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    if not isinstance(report, dict):
        raise ValueError("SARIF root must be a mapping")
    findings = blocking_findings(report, minimum_score=args.minimum_score)
    if findings:
        print("blocking dependency vulnerabilities: " + ", ".join(findings))
        return 1
    print(f"dependency policy passed: no CVSS >= {args.minimum_score:g} findings")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
