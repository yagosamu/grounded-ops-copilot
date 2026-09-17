"""Build bounded chunks while retaining normalized document spans."""

import re
from dataclasses import dataclass

from modules.parsing.parser import ParsedDocument


@dataclass(frozen=True)
class ProvenanceSpan:
    start: int
    end: int
    title: str | None
    level: int


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    text: str
    start: int
    end: int
    token_count: int
    spans: tuple[ProvenanceSpan, ...]
    parser_version: str
    chunker_version: str


class StructuralChunker:
    VERSION = "structural-1"
    _TOKEN = re.compile(r"\S+")

    def __init__(self, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")
        self.max_tokens = max_tokens

    def chunk(self, document: ParsedDocument) -> tuple[Chunk, ...]:
        ranges: list[tuple[int, int]] = []
        pending_start: int | None = None
        pending_end = 0

        for section in document.sections:
            section_tokens = self._count(
                document.normalized_text, section.start, section.end
            )
            if section_tokens > self.max_tokens:
                if pending_start is not None:
                    ranges.append((pending_start, pending_end))
                    pending_start = None
                ranges.extend(
                    self._split_range(
                        document.normalized_text, section.start, section.end
                    )
                )
                continue

            proposed_start = section.start if pending_start is None else pending_start
            proposed_tokens = self._count(
                document.normalized_text, proposed_start, section.end
            )
            if pending_start is not None and proposed_tokens > self.max_tokens:
                ranges.append((pending_start, pending_end))
                pending_start = section.start
            elif pending_start is None:
                pending_start = section.start
            pending_end = section.end

        if pending_start is not None:
            ranges.append((pending_start, pending_end))

        return tuple(
            self._build_chunk(document, ordinal, start, end)
            for ordinal, (start, end) in enumerate(ranges)
        )

    def _split_range(self, text: str, start: int, end: int) -> list[tuple[int, int]]:
        tokens = list(self._TOKEN.finditer(text, start, end))
        if not tokens:
            return [(start, end)]
        ranges = []
        cursor = start
        for offset in range(self.max_tokens, len(tokens), self.max_tokens):
            boundary = tokens[offset].start()
            ranges.append((cursor, boundary))
            cursor = boundary
        ranges.append((cursor, end))
        return ranges

    def _build_chunk(
        self, document: ParsedDocument, ordinal: int, start: int, end: int
    ) -> Chunk:
        spans = tuple(
            ProvenanceSpan(
                max(start, section.start),
                min(end, section.end),
                section.title,
                section.level,
            )
            for section in document.sections
            if section.start < end and section.end > start
        )
        return Chunk(
            ordinal,
            document.normalized_text[start:end],
            start,
            end,
            self._count(document.normalized_text, start, end),
            spans,
            document.parser_version,
            self.VERSION,
        )

    @classmethod
    def _count(cls, text: str, start: int, end: int) -> int:
        return sum(1 for _ in cls._TOKEN.finditer(text, start, end))
