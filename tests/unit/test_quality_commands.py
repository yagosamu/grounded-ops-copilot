"""Public command seams for quality-gate tooling."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

PROJECT_ROOT = Path(__file__).parents[2]
FLOOR_GUARD = PROJECT_ROOT / "scripts" / "floor_guard.py"
COMMAND_CONTRACT = PROJECT_ROOT / "scripts" / "command_contract.py"
CLEAN_COVERAGE = PROJECT_ROOT / "scripts" / "clean_coverage.py"


def load_command_contract() -> object:
    """Load the standalone contract script for its process-boundary seam."""
    spec = importlib.util.spec_from_file_location("command_contract", COMMAND_CONTRACT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def load_clean_coverage() -> object:
    """Load the root-scoped coverage cleanup helper."""
    spec = importlib.util.spec_from_file_location("clean_coverage", CLEAN_COVERAGE)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


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


def test_command_contract_strips_parent_coverage_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("COV_CORE_DATAFILE", "should-not-reach-make")

    environment = load_command_contract().child_environment()

    assert "COV_CORE_DATAFILE" not in environment
    assert Path(environment["COVERAGE_FILE"]).parent != PROJECT_ROOT


def test_coverage_cleanup_removes_only_root_coverage_data(tmp_path: Path) -> None:
    coverage_data = tmp_path / ".coverage"
    nested_coverage_data = tmp_path / ".coverage.child"
    retained_file = tmp_path / "retain.txt"
    coverage_data.write_text("stale", encoding="utf-8")
    nested_coverage_data.write_text("stale", encoding="utf-8")
    retained_file.write_text("keep", encoding="utf-8")

    removed = load_clean_coverage().clean_coverage_files(tmp_path)

    assert set(removed) == {coverage_data, nested_coverage_data}
    assert not coverage_data.exists()
    assert not nested_coverage_data.exists()
    assert retained_file.read_text(encoding="utf-8") == "keep"
