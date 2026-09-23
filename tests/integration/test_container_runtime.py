"""Build and exercise the production API image through Docker's public CLI."""

from __future__ import annotations

import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from uuid import uuid4

import pytest

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.integration


def test_api_image_is_minimal_unprivileged_and_serves_liveness() -> None:
    image = f"grounded-ops-api:test-{uuid4().hex[:12]}"
    container = f"grounded-ops-api-{uuid4().hex[:12]}"
    try:
        subprocess.run(
            [
                "docker",
                "buildx",
                "build",
                "--platform=linux/amd64",
                "--load",
                "--build-arg",
                "VCS_REF=container-test",
                "--tag",
                image,
                str(ROOT),
            ],
            check=True,
            timeout=900,
        )

        user = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Config.User}}", image],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert user == "10001:10001"

        healthcheck = subprocess.run(
            [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{json .Config.Healthcheck.Test}}",
                image,
            ],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "health/live" in " ".join(json.loads(healthcheck))

        environment = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{json .Config.Env}}", image],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        assert "OPENAI_API_KEY" not in environment
        assert "DATABASE_URL" not in environment

        dependencies = subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--entrypoint",
                "python",
                image,
                "-c",
                "import importlib.util; assert not any("
                "importlib.util.find_spec(module) "
                "for module in ('pytest', 'ruff', 'mypy'))",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert dependencies.returncode == 0, dependencies.stderr

        subprocess.run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--read-only",
                "--tmpfs",
                "/tmp:rw,noexec,nosuid,size=16m",
                "--cap-drop",
                "ALL",
                "--security-opt",
                "no-new-privileges",
                "--publish",
                "127.0.0.1::8000",
                "--name",
                container,
                image,
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        published = subprocess.run(
            ["docker", "port", container, "8000/tcp"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        port = int(published.rsplit(":", 1)[1])
        _assert_liveness(port)
    finally:
        subprocess.run(
            ["docker", "stop", container],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["docker", "image", "rm", "--force", image],
            check=False,
            capture_output=True,
            text=True,
        )


def _assert_liveness(port: int) -> None:
    deadline = time.monotonic() + 20
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/health/live", timeout=2
            ) as response:
                assert response.status == 200
                assert json.load(response) == {"status": "ok"}
                return
        except (OSError, urllib.error.URLError) as error:
            last_error = error
            time.sleep(0.25)
    raise AssertionError("API image did not become live") from last_error
