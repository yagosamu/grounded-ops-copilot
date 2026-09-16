"""Diff-scoped enforcement for changes that weaken the quality floor."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass

SUPPRESSIONS = re.compile(
    "@ts-"
    "ignore|@ts-"
    "nocheck|eslint-"
    "disable|biome-"
    "ignore|# *no"
    "qa|"
    "# *type: *ignore|# *pyright: *ignore|istanbul "
    "ignore|nosem"
    "grep|"
    "gitleaks:"
    "allow|Stryker "
    "disable"
)
STUBS = re.compile(
    r"throw new (Error|NotImplemented).*[Nn]ot implemented|"
    r"catch\s*\(\w*\)\s*\{\s*\}|catch\s*\{\s*\}|"
    r"raise NotImplemented"
    r"Error|\bTO"
    r"DO\b|\bpass\s*# *stub"
)
SKIPS = re.compile(
    r"\.(sk" r"ip|to" r"do)\b|\bxit\(|\bxdescribe\(|@pytest\.mark\.sk" r"ip|t\.Skip\("
)
TEST_PATH = re.compile(r"(^|/)(tests?/.*|.*(_test|test_)\w*\.py)$")


@dataclass(frozen=True)
class DiffLine:
    """One added or removed line with its repository-relative path."""

    file: str
    text: str


@dataclass(frozen=True)
class Finding:
    """A floor rule violation without reproducing matched source content."""

    rule: str
    file: str


def git(*args: str) -> subprocess.CompletedProcess[str]:
    """Run Git without raising so unavailable history stays distinguishable."""
    return subprocess.run(
        ["git", *args], check=False, capture_output=True, text=True, encoding="utf-8"
    )


def normalized_diff_path(header: str) -> str:
    """Normalize Git's a/ and b/ prefixes for location-only reporting."""
    path = header.removeprefix("a/").removeprefix("b/")
    return path.replace("\\", "/")


def collect_diff(base: str) -> tuple[list[DiffLine], list[DiffLine], list[str]] | None:
    """Collect tracked and untracked changes, or None when no merge base exists."""
    merge_base = git("merge-base", base, "HEAD")
    if merge_base.returncode != 0 or not merge_base.stdout.strip():
        return None

    tracked = git("diff", "--unified=0", merge_base.stdout.strip(), "--")
    untracked = git("ls-files", "--others", "--exclude-standard")
    diff_parts = [tracked.stdout]
    for file in filter(None, untracked.stdout.splitlines()):
        diff_parts.append(
            git("diff", "--no-index", "--unified=0", "/dev/null", file).stdout
        )

    added: list[DiffLine] = []
    removed: list[DiffLine] = []
    file = ""
    for line in "\n".join(diff_parts).splitlines():
        if line.startswith("+++ "):
            file = normalized_diff_path(line[4:])
        elif line.startswith("+") and not line.startswith("+++"):
            added.append(DiffLine(file, line[1:]))
        elif line.startswith("-") and not line.startswith("---"):
            removed.append(DiffLine(file, line[1:]))

    names = git(
        "diff", "--name-status", merge_base.stdout.strip(), "--"
    ).stdout.splitlines()
    return added, removed, names


def has_lowered_threshold(removed: DiffLine, added: list[DiffLine]) -> bool:
    """Report only numeric constraint changes that reduce an existing threshold."""
    old_numbers = [float(value) for value in re.findall(r"\d+(?:\.\d+)?", removed.text)]
    for candidate in added:
        if candidate.file != removed.file:
            continue
        if candidate.text.split("|")[0] != removed.text.split("|")[0]:
            continue
        new_numbers = [
            float(value) for value in re.findall(r"\d+(?:\.\d+)?", candidate.text)
        ]
        if any(new < old for old, new in zip(old_numbers, new_numbers, strict=False)):
            return True
    return False


def find_violations(base: str) -> tuple[int, list[Finding]]:
    """Return exit semantics and redacted locations for all floor violations."""
    diff = collect_diff(base)
    if diff is None:
        return 2, []

    added, removed, names = diff
    findings: list[Finding] = []
    for line in added:
        if SUPPRESSIONS.search(line.text):
            findings.append(Finding("silenced-checker", line.file))
        if STUBS.search(line.text):
            findings.append(Finding("unfinished-work", line.file))
        if SKIPS.search(line.text):
            findings.append(Finding("test-made-easier", line.file))
        if line.file.endswith("CONSTRAINTS.md") and re.match(
            r"^\| *(W|E)\d+ *\|", line.text
        ):
            findings.append(Finding("new-exception", line.file))

    for line in removed:
        if TEST_PATH.search(line.file) and re.search(
            r"\b(expect|assert|should)\b", line.text
        ):
            findings.append(Finding("assertion-removed", line.file))
        if line.file.endswith("CONSTRAINTS.md") and has_lowered_threshold(line, added):
            findings.append(Finding("threshold-lowered", line.file))

    for name in names:
        status, _, path = name.partition("\t")
        if status == "D" and TEST_PATH.search(path.replace("\\", "/")):
            findings.append(Finding("test-deleted", path.replace("\\", "/")))
    return (1 if findings else 0), findings


def main() -> int:
    """Run the guard and preserve exit code 2 for an unusable comparison base."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="origin/main")
    args = parser.parse_args()
    exit_code, findings = find_violations(args.base)
    if exit_code == 2:
        print(f"floor-guard: no merge base against {args.base}", file=sys.stderr)
        return 2
    if not findings:
        print("floor-guard: clean")
        return 0
    print(f"floor-guard: {len(findings)} floor violation(s):", file=sys.stderr)
    for finding in findings:
        print(f"  [{finding.rule}] {finding.file}", file=sys.stderr)
    print("Fix the change or use a reviewed, tracked exception.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
