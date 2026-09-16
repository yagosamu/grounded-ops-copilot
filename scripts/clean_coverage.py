"""Remove only generated coverage data from an explicit project root."""

from __future__ import annotations

from pathlib import Path


def clean_coverage_files(root: Path) -> list[Path]:
    """Delete .coverage data files in root without traversing into subdirectories."""
    removed: list[Path] = []
    for path in root.glob(".coverage*"):
        if path.is_file():
            path.unlink()
            removed.append(path)
    return removed


if __name__ == "__main__":
    clean_coverage_files(Path.cwd())
