"""Wikipedia smartphone list pages → CoveragePoints.

Smartphone coverage is per-brand: each major OEM has a Wikipedia "List of
<brand> smartphones" page. We currently target the high-volume brands.
"""

from __future__ import annotations

from collections.abc import Iterator

from ..normalize import slugify
from .base import CoveragePoint
from .wikipedia import fetch_wikipedia_html, wikitable_first_cells

PAGES: list[tuple[str, str]] = [
    ("samsung", "List_of_Samsung_Galaxy_smartphones"),
    ("apple", "List_of_iPhone_models"),
    ("google", "Pixel_(smartphone)"),
    ("oneplus", "List_of_OnePlus_products"),
    ("xiaomi", "List_of_Xiaomi_smartphones"),
]


class WikipediaSmartphone:
    """Per-brand Wikipedia phone lists."""

    name = "wikipedia-smartphone"
    description = "Wikipedia: brand-specific smartphone lineup pages."

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
            # Phones tend to have shorter names (e.g. "iphone-16"); be lenient
            # but still require something model-like.
            if len(slug) < 3:
                continue
            yield CoveragePoint(
                category="smartphone",
                manufacturer=manufacturer,
                name=raw_name,
                slug=slug,
                source=f"wikipedia:{page}",
                url=f"https://en.wikipedia.org/wiki/{page}",
            )
