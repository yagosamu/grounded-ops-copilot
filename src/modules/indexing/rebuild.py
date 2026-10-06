"""Promote a rebuilt index only after its contents pass validation."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from observability.telemetry import (
    EntryPoint,
    Telemetry,
    TelemetryComponent,
    TelemetryOperation,
)


class IndexCatalog(Protocol):
    def active_index(self) -> str: ...

    def create_candidate(self, schema_version: str) -> str: ...

    def switch_aliases(self, expected_index: str, target_index: str) -> None: ...


@dataclass(frozen=True)
class IndexSwitch:
    previous_index: str
    current_index: str


class IndexValidationError(RuntimeError):
    """The rebuilt index did not pass its promotion checks."""


class IndexRebuilder:
    """Rebuild while ingestion is paused; reads remain on the previous index.

    Failed candidates remain unaliased for inspection. Retry with a new schema
    version or explicitly retire the orphan after an operator review.
    """

    def __init__(
        self, catalog: IndexCatalog, telemetry: Telemetry | None = None
    ) -> None:
        self.catalog = catalog
        self.telemetry = telemetry or Telemetry()

    def rebuild(
        self,
        schema_version: str,
        *,
        build: Callable[[str], None],
        validate: Callable[[str], bool],
    ) -> IndexSwitch:
        with self.telemetry.operation(
            TelemetryComponent.INDEXING,
            TelemetryOperation.INDEX_REBUILD,
            default_entry_point=EntryPoint.CLI,
        ):
            previous = self.catalog.active_index()
            candidate = self.catalog.create_candidate(schema_version)
            build(candidate)
            if not validate(candidate):
                raise IndexValidationError("rebuilt index validation failed")
            try:
                self.catalog.switch_aliases(previous, candidate)
            except Exception:
                if self.catalog.active_index() == candidate:
                    self.rollback(IndexSwitch(previous, candidate))
                raise
            return IndexSwitch(previous, candidate)

    def rollback(self, switch: IndexSwitch) -> None:
        with self.telemetry.operation(
            TelemetryComponent.INDEXING,
            TelemetryOperation.INDEX_ROLLBACK,
            default_entry_point=EntryPoint.CLI,
        ):
            self.catalog.switch_aliases(switch.current_index, switch.previous_index)
