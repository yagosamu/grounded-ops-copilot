"""Pack authorized evidence into a deterministic, bounded model context."""

from collections import defaultdict
from dataclasses import dataclass

from modules.retrieval.retriever import Evidence, EvidenceSet


@dataclass(frozen=True)
class PackedContext:
    evidence: tuple[Evidence, ...]
    token_count: int
    max_tokens: int
    conflicts: tuple[tuple[str, tuple[str, ...]], ...]
    truncated: bool


class ContextPacker:
    def __init__(self, *, max_tokens: int) -> None:
        if max_tokens <= 0:
            raise ValueError("invalid context token budget")
        self._max_tokens = max_tokens

    def pack(self, evidence_set: EvidenceSet) -> PackedContext:
        unique = _deduplicate(evidence_set.evidence)
        candidates = _diverse_order(unique)
        selected: list[Evidence] = []
        token_count = 0
        for item in candidates:
            item_tokens = len(item.text.split())
            if item_tokens > self._max_tokens - token_count:
                continue
            selected.append(item)
            token_count += item_tokens
        packed = tuple(selected)
        return PackedContext(
            packed,
            token_count,
            self._max_tokens,
            _conflicts(packed),
            len(packed) < len(unique),
        )


def _deduplicate(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    seen: set[tuple[str, str, tuple[int, int]]] = set()
    unique: list[Evidence] = []
    for item in evidence:
        identity = (item.chunk_id, item.document_version_id, item.span)
        if identity not in seen:
            seen.add(identity)
            unique.append(item)
    return tuple(unique)


def _diverse_order(evidence: tuple[Evidence, ...]) -> tuple[Evidence, ...]:
    sources: dict[str, list[Evidence]] = {}
    for item in evidence:
        sources.setdefault(item.source_id, []).append(item)
    ordered: list[Evidence] = []
    depth = 0
    while len(ordered) < len(evidence):
        for items in sources.values():
            if depth < len(items):
                ordered.append(items[depth])
        depth += 1
    return tuple(ordered)


def _conflicts(
    evidence: tuple[Evidence, ...],
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    versions: defaultdict[str, set[str]] = defaultdict(set)
    for item in evidence:
        versions[item.document_id].add(item.document_version_id)
    return tuple(
        (document_id, tuple(sorted(document_versions)))
        for document_id, document_versions in sorted(versions.items())
        if len(document_versions) > 1
    )
