"""Run a marker expression while treating an empty future test layer as empty."""

from __future__ import annotations

import argparse
import subprocess


def main() -> int:
    """Preserve test failures and accept only pytest's documented no-tests exit code."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--markers", required=True)
    args = parser.parse_args()
    result = subprocess.run(["uv", "run", "pytest", "-m", args.markers], check=False)
    if result.returncode in {0, 5}:
        return 0
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
