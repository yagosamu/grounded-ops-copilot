"""ING-02 deterministic parsing through the public parser seam."""

import json
from dataclasses import asdict
from pathlib import Path

import pytest

from modules.parsing.parser import MarkdownParser, ParserInputError

pytestmark = pytest.mark.unit
FIXTURES = Path("tests/fixtures/parsing")


def test_structured_markdown_matches_snapshot_and_every_span_round_trips() -> None:
    source = (FIXTURES / "structured.md").read_bytes()
    expected = json.loads((FIXTURES / "structured.json").read_text())

    parsed = MarkdownParser().parse(source)

    assert {
        "parser_version": parsed.parser_version,
        "sections": [asdict(section) for section in parsed.sections],
    } == expected
    assert [
        parsed.normalized_text[section.start : section.end]
        for section in parsed.sections
    ] == [section.text for section in parsed.sections]
    assert "[API reference](https://opentelemetry.io/docs/)" in parsed.sections[0].text
    assert "```python\n# this is code, not a heading" in parsed.sections[1].text
    assert "| Signal | Stable |" in parsed.sections[1].text


def test_line_endings_normalize_deterministically_with_complete_spans() -> None:
    windows = b"Preamble\r\n\r\n# Heading\rBody\r\n"
    expected = "Preamble\n\n# Heading\nBody\n"

    first = MarkdownParser().parse(windows)
    second = MarkdownParser().parse(windows)

    assert first == second
    assert first.normalized_text == expected
    assert [
        (section.title, section.level, section.start, section.end)
        for section in first.sections
    ] == [
        (None, 0, 0, 10),
        ("Heading", 1, 10, 25),
    ]
    assert "".join(section.text for section in first.sections) == expected


@pytest.mark.parametrize(
    "content",
    [b"\xff", b"safe\x00hidden", b"x" * (10 * 1024 * 1024 + 1)],
    ids=("invalid-utf8", "nul-byte", "oversized"),
)
def test_malformed_input_is_rejected_without_echoing_content(content: bytes) -> None:
    with pytest.raises(ParserInputError, match="invalid markdown document") as error:
        MarkdownParser().parse(content)

    assert "hidden" not in str(error.value)
