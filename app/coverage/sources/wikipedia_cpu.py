"""Wikipedia CPU list pages → CoveragePoints."""

from __future__ import annotations

from collections.abc import Iterator

from ..normalize import slugify
from .base import CoveragePoint
from .wikipedia import fetch_wikipedia_html, wikitable_first_cells

# (manufacturer, wikipedia page title). Each page contributes to the
# manufacturer's curated bucket. New pages can be added without code changes.
PAGES: list[tuple[str, str]] = [
    ("intel", "List_of_Intel_Core_processors"),
    ("intel", "List_of_Intel_Xeon_processors"),
    ("intel", "List_of_Intel_microprocessors"),
    ("intel", "List_of_Intel_Atom_processors"),
    ("intel", "List_of_Intel_Pentium_processors"),
    ("intel", "List_of_Intel_Celeron_processors"),
    ("amd", "List_of_AMD_processors"),
    ("amd", "List_of_AMD_Ryzen_processors"),
    ("amd", "List_of_AMD_Epyc_processors"),
    ("amd", "List_of_AMD_Threadripper_processors"),
    ("amd", "List_of_AMD_Opteron_processors"),
]


class WikipediaCpu:
    """Aggregates the Wikipedia ``List_of_*_processors`` family of pages."""

    name = "wikipedia-cpu"
    description = "Wikipedia: List_of_*_processors pages for Intel and AMD."

    def __init__(self, pages: list[tuple[str, str]] | None = None) -> None:
        self._pages = pages if pages is not None else PAGES

    def fetch(self) -> Iterator[CoveragePoint]:
        for manufacturer, page in self._pages:
            try:
                html = fetch_wikipedia_html(page)
            except Exception:
                # Sources are best-effort; a single broken page should not
                # tank the whole report.
                continue
            yield from self._extract(html, manufacturer, page)

    @staticmethod
    def _extract(html: str, manufacturer: str, page: str) -> Iterator[CoveragePoint]:
        for raw_name in wikitable_first_cells(html):
            slug = slugify(raw_name, manufacturer=manufacturer)
            # Filter obvious non-models. Real CPU slugs always contain a digit
            # and are at least a few characters long.
            if len(slug) < 4 or not any(c.isdigit() for c in slug):
                continue
            yield CoveragePoint(
                category="cpu",
                manufacturer=manufacturer,
                name=raw_name,
                slug=slug,
                source=f"wikipedia:{page}",
                url=f"https://en.wikipedia.org/wiki/{page}",
            )
