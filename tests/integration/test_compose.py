"""Static infrastructure smoke tests for the local dependency contract."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


def test_compose_renders_core_services_and_documented_volumes() -> None:
    environment = os.environ | {
        "POSTGRES_PASSWORD": "test-only-password",
        "MINIO_ROOT_USER": "test-only-user",
        "MINIO_ROOT_PASSWORD": "test-only-password",
    }
    result = subprocess.run(
        ["docker", "compose", "-f", "compose.yml", "config", "--format", "json"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 0, result.stderr
    for service in ("postgres", "opensearch", "minio"):
        assert f'"{service}"' in result.stdout
    for volume in ("postgres_data", "opensearch_data", "minio_data"):
        assert volume in result.stdout


def test_example_environment_has_no_real_local_values() -> None:
    example = Path(".env.example").read_text(encoding="utf-8")

    assert "replace-with-a-local-password" in example
    assert "replace-with-a-local-user" in example
