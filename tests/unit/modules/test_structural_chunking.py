"""ING-02 deterministic chunking through the public chunker seam."""

import pytest

from modules.chunking.structural import Chunk, StructuralChunker
from modules.parsing.parser import MarkdownParser

pytestmark = pytest.mark.unit


def assert_complete_provenance(source: str, chunks: tuple[Chunk, ...]) -> None:
    assert "".join(chunk.text for chunk in chunks) == source
    assert [chunk.ordinal for chunk in chunks] == list(range(len(chunks)))
    spans = [span for chunk in chunks for span in chunk.spans]
    assert spans[0].start == 0
    assert spans[-1].end == len(source)
    assert all(
        left.end == right.start for left, right in zip(spans, spans[1:], strict=False)
    )
    assert all(source[span.start : span.end] for span in spans)


def test_chunks_respect_token_bound_and_preserve_all_section_spans() -> None:
    document = MarkdownParser().parse(
        b"# Alpha\none two\n## Beta\nthree four five\n## Gamma\nsix seven\n"
    )

    chunks = StructuralChunker(max_tokens=5).chunk(document)

    assert [chunk.token_count for chunk in chunks] == [4, 5, 4]
    assert all(chunk.token_count <= 5 for chunk in chunks)
    assert [chunk.chunker_version for chunk in chunks] == ["structural-1"] * 3
    assert [chunk.parser_version for chunk in chunks] == ["markdown-1"] * 3
    assert_complete_provenance(document.normalized_text, chunks)


def test_tiny_sections_are_combined_without_losing_their_boundaries() -> None:
    document = MarkdownParser().parse(b"# A\nx\n## B\ny\n")

    chunks = StructuralChunker(max_tokens=6).chunk(document)

    assert len(chunks) == 1
    assert chunks[0].token_count == 6
    assert [(span.title, span.level) for span in chunks[0].spans] == [
        ("A", 1),
        ("B", 2),
    ]
    assert_complete_provenance(document.normalized_text, chunks)


def test_oversized_code_block_is_split_at_token_boundaries() -> None:
    document = MarkdownParser().parse(
        b"# Code\n```python\none two three four five six seven eight nine\n```\n"
    )

    chunks = StructuralChunker(max_tokens=5).chunk(document)

    assert [chunk.token_count for chunk in chunks] == [5, 5, 3]
    assert chunks[0].text.startswith("# Code\n```python")
    assert chunks[-1].text.endswith("```\n")
    assert_complete_provenance(document.normalized_text, chunks)


def test_chunking_is_stable_and_bounded_for_varied_document_lengths() -> None:
    chunker = StructuralChunker(max_tokens=7)
    for count in range(1, 41):
        document = MarkdownParser().parse(" ".join(["word"] * count).encode())

        first = chunker.chunk(document)
        second = chunker.chunk(document)

        assert first == second
        assert all(0 < chunk.token_count <= 7 for chunk in first)
        assert_complete_provenance(document.normalized_text, first)


@pytest.mark.parametrize("maximum", [0, -1])
def test_non_positive_token_bounds_are_rejected(maximum: int) -> None:
    with pytest.raises(ValueError, match="max_tokens must be positive"):
        StructuralChunker(max_tokens=maximum)
