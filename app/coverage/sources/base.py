"""Shared types for coverage sources."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CoveragePoint:
    """One SKU surfaced by an upstream catalog.

    Sources should set ``slug`` via :func:`app.coverage.normalize.slugify` so
    that comparisons with curated slugs are apples-to-apples.
    """

    category: str  # "cpu" | "gpu" | "smartphone" | "soc"
    manufacturer: str  # brand slug, e.g. "intel"
    name: str  # raw display name, e.g. "Intel Core i9-14900K"
    slug: str  # normalized, e.g. "core-i9-14900k"
    source: str  # short ID, e.g. "wikipedia:List_of_Intel_Core_processors"
    url: str  # link back to the source page


class Source(Protocol):
    """Coverage source contract."""

    name: str
    description: str

    def fetch(self) -> Iterator[CoveragePoint]:
        """Yield every SKU the source surfaces. May silently skip failed pages."""
        ...
