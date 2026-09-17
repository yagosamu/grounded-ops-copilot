"""Normalize Markdown into structural sections with exact character spans."""

import re
from dataclasses import dataclass


class ParserInputError(ValueError):
    """A safe parsing failure that never includes source content."""


@dataclass(frozen=True)
class ParsedSection:
    title: str | None
    level: int
    text: str
    start: int
    end: int


@dataclass(frozen=True)
class ParsedDocument:
    parser_version: str
    normalized_text: str
    sections: tuple[ParsedSection, ...]


class MarkdownParser:
    VERSION = "markdown-1"
    MAX_BYTES = 10 * 1024 * 1024
    _HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*(?:\n)?$")

    def parse(self, content: bytes) -> ParsedDocument:
        if len(content) > self.MAX_BYTES or b"\x00" in content:
            raise ParserInputError("invalid markdown document")
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            raise ParserInputError("invalid markdown document") from None

        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        sections: list[ParsedSection] = []
        section_start = 0
        section_title: str | None = None
        section_level = 0
        cursor = 0
        fence: str | None = None

        for line in normalized.splitlines(keepends=True):
            stripped = line.lstrip()
            marker = stripped[:3]
            if marker in {"```", "~~~"}:
                if fence is None:
                    fence = marker
                elif fence == marker:
                    fence = None
            elif fence is None:
                heading = self._HEADING.match(line)
                if heading is not None:
                    if cursor > section_start:
                        sections.append(
                            self._section(
                                normalized,
                                section_start,
                                cursor,
                                section_title,
                                section_level,
                            )
                        )
                    section_start = cursor
                    section_level = len(heading.group(1))
                    section_title = heading.group(2)
            cursor += len(line)

        if cursor > section_start:
            sections.append(
                self._section(
                    normalized,
                    section_start,
                    cursor,
                    section_title,
                    section_level,
                )
            )
        return ParsedDocument(self.VERSION, normalized, tuple(sections))

    @staticmethod
    def _section(
        text: str,
        start: int,
        end: int,
        title: str | None,
        level: int,
    ) -> ParsedSection:
        return ParsedSection(title, level, text[start:end], start, end)
