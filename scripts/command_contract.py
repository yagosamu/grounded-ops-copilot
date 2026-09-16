"""Exercise every canonical Make target against a reversible failure fixture."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

TARGETS = ("check", "test", "pre-push", "release-check", "operational-test")


def child_environment() -> dict[str, str]:
    """Keep nested Make runs outside the parent pytest-cov process."""
    return {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE")
    }


def make_command(target: str, base: str) -> list[str]:
    """Build a portable Make invocation and honor an explicitly supplied scanner."""
    command = ["make", target, f"BASE={base}"]
    scanner = shutil.which("gitleaks")
    if scanner:
        command.append(f"GITLEAKS={scanner}")
    return command


def run(target: str, base: str) -> subprocess.CompletedProcess[str]:
    """Execute one target while reserving output for a failed contract report."""
    return subprocess.run(
        make_command(target, base),
        check=False,
        capture_output=True,
        text=True,
        env=child_environment(),
    )


def main() -> int:
    """Prove targets fail closed, then recover after fixture cleanup."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", required=True)
    parser.add_argument("--target", choices=TARGETS)
    args = parser.parse_args()
    root = Path.cwd()
    source_failure = root / "src" / "grounded_ops" / "_quality_contract_failure.py"
    operational_dir = root / "tests" / "operational"
    operational_failure = operational_dir / "test_quality_contract_failure.py"

    targets = (args.target,) if args.target else TARGETS
    for target in targets:
        dry_run = subprocess.run(
            ["make", "-n", target],
            check=False,
            capture_output=True,
            text=True,
            env=child_environment(),
        )
        if dry_run.returncode != 0:
            print(f"{target}: target is missing", file=sys.stderr)
            return 1

    operational_dir.mkdir(parents=True, exist_ok=True)
    source_failure.write_text("def invalid_python(:\n", encoding="utf-8")
    operational_failure.write_text(
        "import pytest\n\npytestmark = pytest.mark.operational\n\n"
        "def test_intentional_failure() -> None:\n    assert False\n",
        encoding="utf-8",
    )
    try:
        for target in targets:
            if run(target, args.base).returncode == 0:
                print(f"{target}: accepted an intentional failure", file=sys.stderr)
                return 1
    finally:
        source_failure.unlink(missing_ok=True)
        operational_failure.unlink(missing_ok=True)
        if not any(operational_dir.iterdir()):
            operational_dir.rmdir()

    for target in targets:
        if run(target, args.base).returncode != 0:
            print(f"{target}: did not recover after fixture removal", file=sys.stderr)
            return 1
    print("command-contract: all targets failed closed and recovered")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
