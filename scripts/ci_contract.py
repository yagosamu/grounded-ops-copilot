"""Validate CI wiring and bind release evidence to immutable file hashes."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml

FULL_COMMIT = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
VALID_OUTCOMES = {"success", "failure", "cancelled", "skipped"}


def load_yaml(path: Path) -> dict[str, Any]:
    """Load one mapping-based YAML document."""
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"expected a YAML mapping in {path}")
    return document


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a mapping")
    return value


def _steps(job: Mapping[str, Any], label: str) -> dict[str, Mapping[str, Any]]:
    raw_steps = job.get("steps")
    if not isinstance(raw_steps, list):
        raise ValueError(f"{label} steps must be a list")
    indexed: dict[str, Mapping[str, Any]] = {}
    for raw_step in raw_steps:
        step = _mapping(raw_step, f"{label} step")
        step_id = step.get("id")
        if isinstance(step_id, str):
            indexed[step_id] = step
    return indexed


def validate_pipeline(
    *,
    contract: Mapping[str, Any],
    workflow: Mapping[str, Any],
    branch_protection: Mapping[str, Any],
) -> None:
    """Fail when workflow wiring can bypass a declared release gate."""
    if contract.get("schema_version") != "ci-contract.v1":
        raise ValueError("unsupported CI contract version")
    if workflow.get("permissions") != {"contents": "read"}:
        raise ValueError("workflow permissions must be read-only")

    triggers = _mapping(workflow.get("on"), "workflow triggers")
    required_triggers = {"pull_request", "push", "merge_group"}
    if not required_triggers.issubset(triggers):
        raise ValueError(
            "workflow must cover pull requests, main pushes and merge queues"
        )

    jobs = _mapping(workflow.get("jobs"), "workflow jobs")
    required_status = str(contract.get("required_status"))
    final_job = _mapping(jobs.get("required"), "required job")
    if final_job.get("name") != required_status:
        raise ValueError("required job name does not match the CI contract")
    if "always()" not in str(final_job.get("if", "")):
        raise ValueError("required job must run after upstream failures")

    final_needs = final_job.get("needs")
    if set(final_needs if isinstance(final_needs, list) else []) != {
        "release",
        "security",
    }:
        raise ValueError("required status must aggregate release and security jobs")
    final_commands = json.dumps(final_job, sort_keys=True)
    for owner in ("release", "security"):
        if f"needs.{owner}.result" not in final_commands:
            raise ValueError(f"required status does not inspect {owner}")

    gate_contract = _mapping(contract.get("gates"), "CI gates")
    for gate, raw_owner in gate_contract.items():
        owner = _mapping(raw_owner, f"gate {gate}")
        job_name = str(owner.get("job"))
        step_id = str(owner.get("step"))
        job = _mapping(jobs.get(job_name), f"job {job_name}")
        if step_id not in _steps(job, job_name):
            raise ValueError(f"gate {gate} has no blocking workflow step")

    for job_name in ("release", "security"):
        job = _mapping(jobs.get(job_name), f"job {job_name}")
        timeout = job.get("timeout-minutes")
        if not isinstance(timeout, int) or timeout > 15:
            raise ValueError(f"{job_name} must respect the release time budget")
        for step in _steps(job, job_name).values():
            action = step.get("uses")
            if action is not None and not FULL_COMMIT.fullmatch(str(action)):
                raise ValueError(f"{job_name} contains an action not pinned by commit")
            if step.get("continue-on-error") is True:
                raise ValueError(f"{job_name} contains a non-blocking gate")

    release_commands = "\n".join(
        str(step.get("run", ""))
        for step in _steps(_mapping(jobs["release"], "release"), "release").values()
    )
    for command in ("make release-check", "tests/integration/compose.yml"):
        if command not in release_commands:
            raise ValueError(f"release job is missing {command}")

    security_commands = "\n".join(
        str(step.get("run", ""))
        for step in _steps(_mapping(jobs["security"], "security"), "security").values()
    )
    for scanner in ("gitleaks", "semgrep", "osv-scanner", "security_policy.py"):
        if scanner not in security_commands:
            raise ValueError(f"security job is missing {scanner}")

    protection_status = _mapping(
        branch_protection.get("required_status_checks"),
        "branch-protection required status checks",
    )
    if protection_status.get("checks") != [required_status]:
        raise ValueError("branch protection is not bound to the required CI status")
    if protection_status.get("strict") is not True:
        raise ValueError("branch protection must require an up-to-date branch")


def aggregate_status(
    outcomes: Mapping[str, str], *, required_gates: Iterable[str]
) -> str:
    """Model the fail-closed required status used by discrimination tests."""
    required = set(required_gates)
    missing = required.difference(outcomes)
    if missing:
        raise ValueError(f"missing gate outcomes: {', '.join(sorted(missing))}")
    invalid = {value for value in outcomes.values() if value not in VALID_OUTCOMES}
    if invalid:
        raise ValueError(f"invalid gate outcomes: {', '.join(sorted(invalid))}")
    return (
        "success"
        if all(outcomes[gate] == "success" for gate in required)
        else "failure"
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_evidence_manifest(
    *, root: Path, revision: str, report_paths: Sequence[Path]
) -> dict[str, Any]:
    """Create deterministic evidence whose content changes with any report."""
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("revision must be a full Git commit SHA")
    reports = []
    for relative_path in sorted(report_paths, key=lambda item: item.as_posix()):
        path = root / relative_path
        if not path.is_file():
            raise ValueError(f"missing release report: {relative_path.as_posix()}")
        reports.append(
            {
                "path": relative_path.as_posix(),
                "sha256": _sha256(path),
                "size_bytes": path.stat().st_size,
            }
        )
    return {
        "schema_version": "release-evidence.v1",
        "revision": revision,
        "reports": reports,
    }


def validate_evidence_manifest(manifest: Mapping[str, Any], *, root: Path) -> None:
    """Verify that every evidenced report still has its recorded content."""
    if manifest.get("schema_version") != "release-evidence.v1":
        raise ValueError("unsupported release evidence version")
    reports = manifest.get("reports")
    if not isinstance(reports, list) or not reports:
        raise ValueError("release evidence must contain reports")
    for raw_report in reports:
        report = _mapping(raw_report, "release evidence report")
        relative_path = Path(str(report.get("path")))
        path = root / relative_path
        if not path.is_file():
            raise ValueError(f"missing evidenced report: {relative_path.as_posix()}")
        if report.get("sha256") != _sha256(path):
            raise ValueError(f"hash mismatch for {relative_path.as_posix()}")
        if report.get("size_bytes") != path.stat().st_size:
            raise ValueError(f"size mismatch for {relative_path.as_posix()}")


def _report_paths(contract: Mapping[str, Any]) -> tuple[Path, ...]:
    raw_paths = contract.get("reports")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise ValueError("CI contract must declare release reports")
    return tuple(Path(str(path)) for path in raw_paths)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument(
        "--contract", type=Path, default=Path(".github/ci-contract.yml")
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument(
        "--workflow", type=Path, default=Path(".github/workflows/ci.yml")
    )
    validate.add_argument(
        "--branch-protection",
        type=Path,
        default=Path(".github/branch-protection.yml"),
    )
    evidence = subparsers.add_parser("evidence")
    evidence.add_argument("--revision", required=True)
    evidence.add_argument("--output", required=True, type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run structural validation or create the release-evidence manifest."""
    args = _parser().parse_args(argv)
    root = args.root.resolve()
    contract_path = args.contract
    if not contract_path.is_absolute():
        contract_path = root / contract_path
    contract = load_yaml(contract_path)
    if args.command == "validate":
        workflow_path = args.workflow
        protection_path = args.branch_protection
        if not workflow_path.is_absolute():
            workflow_path = root / workflow_path
        if not protection_path.is_absolute():
            protection_path = root / protection_path
        validate_pipeline(
            contract=contract,
            workflow=load_yaml(workflow_path),
            branch_protection=load_yaml(protection_path),
        )
        return 0

    manifest = build_evidence_manifest(
        root=root,
        revision=args.revision,
        report_paths=_report_paths(contract),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
