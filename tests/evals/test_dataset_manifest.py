"""Referential-integrity tests for the versioned evaluation dataset."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest
import yaml

pytestmark = pytest.mark.retrieval_eval

DATASET = Path("evals/datasets/v1")


def manifest() -> dict[str, object]:
    """Load the published dataset manifest through its filesystem seam."""
    return yaml.safe_load((DATASET / "manifest.yaml").read_text(encoding="utf-8"))


def test_manifest_has_unique_source_and_question_ids() -> None:
    data = manifest()
    source_ids = [source["id"] for source in data["sources"]]
    question_ids = [question["id"] for question in data["golden_questions"]]

    assert len(source_ids) == len(set(source_ids))
    assert len(question_ids) == len(set(question_ids))


def test_sources_are_official_https_documents_with_license_metadata() -> None:
    data = manifest()

    for source in data["sources"]:
        parsed = urlparse(source["url"])
        assert parsed.scheme == "https"
        assert parsed.netloc == "opentelemetry.io"
        assert source["license"] == "Apache-2.0"


def test_evidence_spans_resolve_in_versioned_fixtures() -> None:
    data = manifest()

    for source in data["sources"]:
        fixture = (DATASET / source["fixture"]).read_text(encoding="utf-8")
        assert source["evidence_span"] in fixture


def test_golden_questions_reference_declared_sources() -> None:
    data = manifest()
    source_ids = {source["id"] for source in data["sources"]}

    for question in data["golden_questions"]:
        assert question["evidence_source_id"] in source_ids
