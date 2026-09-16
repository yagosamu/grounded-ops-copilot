"""Public command seams for quality-gate tooling."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parents[2]
FLOOR_GUARD = PROJECT_ROOT / "scripts" / "floor_guard.py"


def git(repository: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run Git in an isolated repository used only by the command seam."""
    return subprocess.run(
        ["git", *args], cwd=repository, check=True, capture_output=True, text=True
    )


def initialize_repository(repository: Path) -> str:
    """Create the committed baseline required by the floor guard contract."""
    git(repository, "init")
    git(repository, "config", "user.email", "test@example.invalid")
    git(repository, "config", "user.name", "Quality Command Test")
    (repository / "baseline.py").write_text("value = 1\n", encoding="utf-8")
    git(repository, "add", "baseline.py")
    git(repository, "commit", "-m", "test: add baseline")
    return git(repository, "rev-parse", "HEAD").stdout.strip()


def run_floor_guard(repository: Path, base: str) -> subprocess.CompletedProcess[str]:
    """Execute the guard through its command-line interface."""
    return subprocess.run(
        [sys.executable, str(FLOOR_GUARD), "--base", base],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )


def test_floor_guard_reports_a_rule_and_path_without_matching_content(
    tmp_path: Path,
) -> None:
    base = initialize_repository(tmp_path)
    opaque_fixture_value = "do-not-print-this-value"
    (tmp_path / "new.py").write_text(
        "# type: " + "ignore\nvalue = '" + opaque_fixture_value + "'\n",
        encoding="utf-8",
    )

    result = run_floor_guard(tmp_path, base)

    assert result.returncode == 1
    assert "[silenced-checker] new.py" in result.stderr
    assert opaque_fixture_value not in result.stderr


def test_floor_guard_returns_two_when_no_merge_base_exists(tmp_path: Path) -> None:
    initialize_repository(tmp_path)

    result = run_floor_guard(tmp_path, "missing-base")

    assert result.returncode == 2
    assert "no merge base against missing-base" in result.stderr
