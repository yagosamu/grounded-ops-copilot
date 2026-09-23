"""Discrimination tests for the blocking continuous-integration contract."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = ROOT / ".github" / "ci-contract.yml"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "ci.yml"
BRANCH_PROTECTION_PATH = ROOT / ".github" / "branch-protection.yml"
VALIDATOR_PATH = ROOT / "scripts" / "ci_contract.py"
SECURITY_POLICY_PATH = ROOT / "scripts" / "security_policy.py"
VERSIONED_REPORTS = (
    Path("evals/agentic/reports/agentic-v1.json"),
    Path("evals/answering/reports/answering-v1.json"),
    Path("evals/retrieval/reports/bm25-v1.json"),
    Path("evals/retrieval/reports/candidates-v1.json"),
    Path("evals/retrieval/reports/chunking-v1.json"),
    Path("ops/observability/reports/alert-simulation-v1.json"),
    Path("tests/operational/load/reports/capacity-local-v1.json"),
)
SETUP_GO_NODE24 = "actions/setup-go@924ae3a1cded613372ab5595356fb5720e22ba16"
REQUIRED_GATES = {
    "lint",
    "types",
    "tests",
    "evals",
    "migrations",
    "secrets",
    "security",
    "container",
}


def _load_validator() -> Any:
    spec = importlib.util.spec_from_file_location("ci_contract", VALIDATOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the CI contract validator")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_security_policy() -> Any:
    spec = importlib.util.spec_from_file_location(
        "security_policy", SECURITY_POLICY_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Could not load the security policy")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


validator = _load_validator()
security_policy = _load_security_policy()
pytestmark = pytest.mark.operational


def test_pipeline_is_secure_bounded_and_bound_to_branch_protection() -> None:
    contract = validator.load_yaml(CONTRACT_PATH)
    workflow = validator.load_yaml(WORKFLOW_PATH)
    branch_protection = validator.load_yaml(BRANCH_PROTECTION_PATH)

    validator.validate_pipeline(
        contract=contract,
        workflow=workflow,
        branch_protection=branch_protection,
    )
    assert set(contract["gates"]) == REQUIRED_GATES
    assert workflow["permissions"] == {"contents": "read"}
    assert workflow["jobs"]["release"]["timeout-minutes"] <= 15
    assert workflow["jobs"]["security"]["timeout-minutes"] <= 15


def test_security_job_uses_osv_compatible_go_and_node24_action() -> None:
    workflow = validator.load_yaml(WORKFLOW_PATH)
    steps = workflow["jobs"]["security"]["steps"]
    go_step = next(step for step in steps if step["name"] == "Install Go")

    assert go_step["uses"] == SETUP_GO_NODE24
    assert tuple(map(int, go_step["with"]["go-version"].split("."))) >= (1, 27, 0)


def test_versioned_reports_have_platform_independent_lf_bytes() -> None:
    attributes = subprocess.run(
        ["git", "check-attr", "eol", "--", *(str(path) for path in VERSIONED_REPORTS)],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    assert len(attributes) == len(VERSIONED_REPORTS)
    assert all(line.endswith(": eol: lf") for line in attributes)
    assert b"\r\n" not in (ROOT / VERSIONED_REPORTS[0]).read_bytes()
    committed = tuple(
        subprocess.run(
            ["git", "show", f"HEAD:{path.as_posix()}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
        ).stdout
        for path in VERSIONED_REPORTS
    )
    assert all(b"\r\n" not in content for content in committed)


@pytest.mark.parametrize("failed_gate", sorted(REQUIRED_GATES))
def test_each_intentional_gate_failure_blocks_required_status(
    failed_gate: str,
) -> None:
    outcomes = dict.fromkeys(REQUIRED_GATES, "success")
    outcomes[failed_gate] = "failure"

    assert validator.aggregate_status(outcomes, required_gates=REQUIRED_GATES) == (
        "failure"
    )


def test_required_status_passes_only_when_every_gate_passes() -> None:
    outcomes = dict.fromkeys(REQUIRED_GATES, "success")

    assert validator.aggregate_status(outcomes, required_gates=REQUIRED_GATES) == (
        "success"
    )
    with pytest.raises(ValueError, match="missing gate outcomes"):
        validator.aggregate_status({"lint": "success"}, required_gates=REQUIRED_GATES)


def test_report_manifest_detects_changed_evidence(tmp_path: Path) -> None:
    report = tmp_path / "report.json"
    report.write_text('{"result": "pass"}\n', encoding="utf-8")
    manifest = validator.build_evidence_manifest(
        root=tmp_path,
        revision="0123456789abcdef0123456789abcdef01234567",
        report_paths=(Path("report.json"),),
    )

    validator.validate_evidence_manifest(manifest, root=tmp_path)
    report.write_text('{"result": "changed"}\n', encoding="utf-8")

    with pytest.raises(ValueError, match="hash mismatch"):
        validator.validate_evidence_manifest(manifest, root=tmp_path)


def test_cli_writes_a_valid_manifest_for_committed_reports(tmp_path: Path) -> None:
    output = tmp_path / "release-evidence.json"

    exit_code = validator.main(
        [
            "--root",
            str(ROOT),
            "--contract",
            str(CONTRACT_PATH),
            "evidence",
            "--revision",
            "fedcba9876543210fedcba9876543210fedcba98",
            "--output",
            str(output),
        ]
    )

    manifest = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert manifest["schema_version"] == "release-evidence.v1"
    assert manifest["revision"] == "fedcba9876543210fedcba9876543210fedcba98"
    assert len(manifest["reports"]) >= 7
    validator.validate_evidence_manifest(manifest, root=ROOT)


@pytest.mark.parametrize(
    ("severity", "expected"),
    [("6.9", []), ("7.0", ["OSV-TEST-1"]), ("9.8", ["OSV-TEST-1"])],
)
def test_dependency_policy_blocks_only_high_or_critical_findings(
    severity: str, expected: list[str]
) -> None:
    report = {
        "runs": [
            {
                "tool": {
                    "driver": {
                        "rules": [
                            {
                                "id": "OSV-TEST-1",
                                "properties": {"security-severity": severity},
                            }
                        ]
                    }
                }
            }
        ]
    }

    assert security_policy.blocking_findings(report, minimum_score=7.0) == expected
