"""Public configuration and application-import smoke tests."""

from __future__ import annotations

import importlib
import os
import pathlib
import shutil
import subprocess

import pytest

pytestmark = pytest.mark.unit


def test_application_factory_creates_fastapi_app() -> None:
    application = importlib.import_module("grounded_ops.app")

    app = application.create_app()

    assert app.title == "GroundedOps"


def test_project_declares_python_313_requirement() -> None:
    project_file = pathlib.Path("pyproject.toml")

    assert project_file.exists()
    assert 'requires-python = ">=3.13,<3.14"' in project_file.read_text(
        encoding="utf-8"
    )


def test_python_version_mismatch_is_rejected() -> None:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("COV_CORE")
    }
    result = subprocess.run(
        [shutil.which("uv") or "uv", "sync", "--locked", "--python", "3.12"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    assert "requires-python" in (result.stdout + result.stderr)


@pytest.mark.parametrize("version", [(3, 12), (3, 14)])
def test_supported_python_range_excludes_adjacent_versions(
    version: tuple[int, int],
) -> None:
    assert not ((3, 13) <= version < (3, 14))
