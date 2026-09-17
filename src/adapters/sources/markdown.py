"""Read licensed Markdown snapshots and classify source changes."""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from hashlib import sha256
from pathlib import Path

import yaml

from domain.ingestion import Source


class SourceInputError(ValueError):
    """A redacted snapshot failure, never a synthetic deletion event."""


@dataclass(frozen=True)
class SourceEvent:
    kind: str
    source: Source
    canonical_key: str
    source_version: str
    source_timestamp: datetime
    content: bytes | None
    license: str
    synthetic: bool = False
    error_class: str | None = None


class MarkdownCorpus:
    MAX_BYTES = 10 * 1024 * 1024

    def __init__(self, root: Path, tenant_id: str, policy: tuple[str, ...]) -> None:
        self.root = root.resolve()
        self.tenant_id = tenant_id
        self.policy = policy

    def scan(
        self,
        captured_at: datetime,
        previous: Mapping[str, SourceEvent] | None = None,
    ) -> list[SourceEvent]:
        previous = previous or {}
        if captured_at.tzinfo is None or any(
            item.source.tenant_id != self.tenant_id for item in previous.values()
        ):
            raise SourceInputError("invalid snapshot context")
        events = []
        for source, fixture, license_id, synthetic in self._entries():
            old = previous.get(source.id)
            base = SourceEvent(
                "malformed",
                source,
                fixture,
                "0" * 64,
                captured_at,
                None,
                license_id,
                synthetic,
                "invalid_source",
            )
            try:
                path = (self.root / fixture).resolve()
                path.relative_to(self.root)
                if path.suffix.lower() != ".md":
                    raise ValueError("not Markdown")
                with path.open("rb") as stream:
                    content = stream.read(self.MAX_BYTES + 1)
                if len(content) > self.MAX_BYTES or b"\x00" in content:
                    raise ValueError("invalid document")
                content.decode("utf-8")
            except FileNotFoundError:
                events.append(
                    replace(old, kind="deleted", content=None) if old else base
                )
                continue
            except (OSError, ValueError):
                events.append(base)
                continue
            digest = sha256(content).hexdigest()
            kind = (
                "new"
                if old is None
                else "unchanged"
                if old.source_version == digest
                else "changed"
            )
            events.append(
                SourceEvent(
                    kind,
                    source,
                    fixture,
                    digest,
                    captured_at,
                    content,
                    license_id,
                    synthetic,
                )
            )
        seen = {event.source.id for event in events}
        events.extend(
            replace(old, kind="deleted", content=None)
            for key, old in previous.items()
            if key not in seen
        )
        return sorted(events, key=lambda event: event.source.id)

    def _entries(self) -> list[tuple[Source, str, str, bool]]:
        try:
            with (self.root / "manifest.yaml").open("rb") as stream:
                data = stream.read(1024 * 1024 + 1)
            if len(data) > 1024 * 1024:
                raise ValueError("manifest size")
            manifest = yaml.safe_load(data)
            if not isinstance(manifest, dict) or not isinstance(
                manifest.get("sources"), list
            ):
                raise ValueError("manifest shape")
            entries = manifest["sources"]
            if not entries or len(entries) > 10000:
                raise ValueError("manifest count")
            result = []
            seen = set()
            for entry in entries:
                if not isinstance(entry, dict) or any(
                    not isinstance(entry.get(key), str)
                    or not entry[key]
                    or len(entry[key]) > 2048
                    for key in ("id", "url", "license", "fixture")
                ):
                    raise ValueError("manifest entry")
                if entry["id"] in seen or not isinstance(
                    entry.get("synthetic", False), bool
                ):
                    raise ValueError("duplicate or invalid metadata")
                seen.add(entry["id"])
                source = Source(
                    entry["id"], self.tenant_id, "markdown", entry["url"], self.policy
                )
                result.append(
                    (
                        source,
                        entry["fixture"],
                        entry["license"],
                        entry.get("synthetic", False),
                    )
                )
            return result
        except (OSError, ValueError, yaml.YAMLError):
            raise SourceInputError("invalid corpus manifest") from None
