"""ING-01 source events from the frozen, licensed public corpus."""

import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest

from adapters.sources.markdown import MarkdownCorpus, SourceInputError

pytestmark = pytest.mark.unit
STAMP = datetime(2026, 1, 1, tzinfo=UTC)


def test_new_unchanged_changed_and_deleted_source_events(tmp_path: Path) -> None:
    shutil.copytree("evals/datasets/v1", tmp_path / "corpus")
    corpus = MarkdownCorpus(tmp_path / "corpus", "alpha", ("engineers",))
    first = corpus.scan(STAMP)
    assert [event.kind for event in first] == ["new", "new"]
    tracing = next(event for event in first if event.source.id == "otel-tracing-api")
    assert tracing.source.tenant_id == "alpha"
    assert tracing.source.policy == ("engineers",)
    assert tracing.license == "Apache-2.0"
    assert tracing.synthetic is False
    assert tracing.source_timestamp == STAMP
    assert b"TracerProvider" in tracing.content
    previous = {event.source.id: event for event in first}
    assert [event.kind for event in corpus.scan(STAMP, previous)] == [
        "unchanged",
        "unchanged",
    ]
    path = tmp_path / "corpus/sources/tracing-api.md"
    path.write_text(
        "# Updated\nNew tracing behavior.\n", encoding="utf-8", newline="\n"
    )
    changed = next(
        event
        for event in corpus.scan(STAMP, previous)
        if event.source.id == "otel-tracing-api"
    )
    assert changed.kind == "changed"
    assert changed.source_version != tracing.source_version
    assert changed.content == b"# Updated\nNew tracing behavior.\n"
    path.unlink()
    deleted = next(
        event
        for event in corpus.scan(STAMP, previous)
        if event.source.id == "otel-tracing-api"
    )
    assert deleted.kind == "deleted"
    assert deleted.content is None
    assert deleted.canonical_key == tracing.canonical_key


@pytest.mark.parametrize(
    "content",
    [b"\xff", b"secret\x00value", b"x" * (10 * 1024 * 1024 + 1)],
    ids=("invalid-utf8", "nul-byte", "oversized"),
)
def test_malformed_documents_emit_safe_events_without_content(
    tmp_path: Path, content: bytes
) -> None:
    shutil.copytree("evals/datasets/v1", tmp_path / "corpus")
    (tmp_path / "corpus/sources/tracing-api.md").write_bytes(content)
    events = MarkdownCorpus(tmp_path / "corpus", "alpha", ("read",)).scan(STAMP)
    malformed = next(event for event in events if event.source.id == "otel-tracing-api")
    assert malformed.kind == "malformed"
    assert malformed.content is None
    assert malformed.error_class == "invalid_source"
    assert "secret" not in repr(malformed)


def test_path_escape_is_rejected_before_reading(tmp_path: Path) -> None:
    root = tmp_path / "corpus"
    shutil.copytree("evals/datasets/v1", root)
    manifest = root / "manifest.yaml"
    manifest.write_text(
        manifest.read_text().replace("sources/tracing-api.md", "../private.md")
    )
    (tmp_path / "private.md").write_text("private outside corpus")
    event = next(
        event
        for event in MarkdownCorpus(root, "alpha", ("read",)).scan(STAMP)
        if event.source.id == "otel-tracing-api"
    )
    assert (event.kind, event.content, event.error_class) == (
        "malformed",
        None,
        "invalid_source",
    )


@pytest.mark.parametrize(
    "manifest", ["sources: [", "sources: []", "sources: [{id: x}]", "sources: nope"]
)
def test_invalid_manifest_never_reports_mass_deletion(
    tmp_path: Path, manifest: str
) -> None:
    (tmp_path / "manifest.yaml").write_text(manifest)
    with pytest.raises(SourceInputError, match="invalid corpus manifest"):
        MarkdownCorpus(tmp_path, "alpha", ("read",)).scan(STAMP)


def test_removed_manifest_entry_emits_tombstone_and_wrong_tenant_cursor_fails(
    tmp_path: Path,
) -> None:
    import yaml

    root = tmp_path / "corpus"
    shutil.copytree("evals/datasets/v1", root)
    corpus = MarkdownCorpus(root, "alpha", ("read",))
    original = corpus.scan(STAMP)
    previous = {event.source.id: event for event in original}
    manifest = yaml.safe_load((root / "manifest.yaml").read_text())
    manifest["sources"] = [
        entry for entry in manifest["sources"] if entry["id"] != "otel-tracing-api"
    ]
    (root / "manifest.yaml").write_text(yaml.safe_dump(manifest))
    event = next(
        event
        for event in corpus.scan(STAMP, previous)
        if event.source.id == "otel-tracing-api"
    )
    assert (event.kind, event.content) == ("deleted", None)
    with pytest.raises(SourceInputError, match="invalid snapshot context"):
        MarkdownCorpus(root, "beta", ("read",)).scan(STAMP, previous)
    with pytest.raises(SourceInputError, match="invalid snapshot context"):
        corpus.scan(datetime(2026, 1, 1))
