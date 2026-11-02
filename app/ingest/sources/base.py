"""Shared types for ingestion sources."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class IngestCandidate:
    """A draft TechAPI record proposed by a source.

    The pipeline writes ``record`` to ``output_path`` (relative to a TechAPI
    ``data/`` root) and records ``source_url`` in the PR description.
    ``missing_fields`` lists schema fields the source could not confidently
    fill; a non-empty list means the candidate is incomplete and should be
    marked as a draft (or skipped). ``problems`` names values the source did
    fill but that cannot be trusted; such a candidate is never written, not
    even as a draft. ``released_by`` is the last day of the release period the
    source states (``"Q4 2026"`` → Dec 31): before it, the part may not have
    shipped. ``aliases`` are other slugs the part may be curated under (a
    laptop-table row named without "Mobile"); dedup checks them too.
    """

    category: str
    manufacturer: str
    slug: str
    record: dict[str, Any]
    source_url: str
    output_path: Path  # e.g. cpu/intel/2024/consumer/core-i9-14900k.json
    missing_fields: tuple[str, ...] = field(default_factory=tuple)
    problems: tuple[str, ...] = field(default_factory=tuple)
    released_by: date | None = None
    aliases: tuple[str, ...] = field(default_factory=tuple)

    @property
    def is_complete(self) -> bool:
        return not self.missing_fields


class IngestSource(Protocol):
    """Ingestion source contract."""

    category: str
    name: str
    description: str

    def fetch(self, *, limit: int | None = None) -> Iterator[IngestCandidate]:
        """Yield candidates. ``limit`` caps the number emitted."""
        ...
