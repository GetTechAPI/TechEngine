"""Wikipedia GPU list pages → CoveragePoints."""

from __future__ import annotations

from collections.abc import Iterator

from ..normalize import is_probable_model_slug, slugify
from .base import CoveragePoint
from .wikipedia import fetch_wikipedia_html, wikitable_first_cells

PAGES: list[tuple[str, str]] = [
    ("nvidia", "List_of_Nvidia_graphics_processing_units"),
    ("amd", "List_of_AMD_graphics_processing_units"),
    ("intel", "List_of_Intel_graphics_processing_units"),
]


class WikipediaGpu:
    """Aggregates Wikipedia ``List_of_*_graphics_processing_units`` pages."""

    name = "wikipedia-gpu"
    description = "Wikipedia: List_of_*_graphics_processing_units for NVIDIA/AMD/Intel."

    def __init__(self, pages: list[tuple[str, str]] | None = None) -> None:
        self._pages = pages if pages is not None else PAGES

    def fetch(self) -> Iterator[CoveragePoint]:
        for manufacturer, page in self._pages:
            try:
                html = fetch_wikipedia_html(page)
            except Exception:
                continue
            yield from self._extract(html, manufacturer, page)

    @staticmethod
    def _extract(html: str, manufacturer: str, page: str) -> Iterator[CoveragePoint]:
        for raw_name in wikitable_first_cells(html):
            slug = slugify(raw_name, manufacturer=manufacturer)
            if not is_probable_model_slug(slug):
                continue
            yield CoveragePoint(
                category="gpu",
                manufacturer=manufacturer,
                name=raw_name,
                slug=slug,
                source=f"wikipedia:{page}",
                url=f"https://en.wikipedia.org/wiki/{page}",
            )
