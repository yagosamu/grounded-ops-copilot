"""Verify that required local checkers reject representative violations."""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def must_fail(command: list[str], cwd: Path) -> None:
    """Reject a checker that accepts an intentionally invalid fixture."""
    result = subprocess.run(
        command, cwd=cwd, check=False, capture_output=True, text=True
    )
    if result.returncode == 0:
        raise RuntimeError(
            f"checker accepted intentional violation: {' '.join(command)}"
        )


def main() -> int:
    """Exercise lint, type, and coverage thresholds without changing project files."""
    with tempfile.TemporaryDirectory() as temporary_directory:
        temporary = Path(temporary_directory)
        lint_fixture = temporary / "lint_fixture.py"
        lint_fixture.write_text("import os\n", encoding="utf-8")
        must_fail(["uv", "run", "ruff", "check", str(lint_fixture)], Path.cwd())

        type_fixture = temporary / "type_fixture.py"
        type_fixture.write_text(
            "def returns_number() -> int:\n    return 'wrong'\n", encoding="utf-8"
        )
        must_fail(["uv", "run", "mypy", "--strict", str(type_fixture)], Path.cwd())

        must_fail(
            [
                "uv",
                "run",
                "pytest",
                "--cov=grounded_ops",
                "--cov-fail-under=101",
                "tests/unit/test_config.py",
            ],
            Path.cwd(),
        )
    print("checker-discrimination: lint, types, and coverage fail closed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
