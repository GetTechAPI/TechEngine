"""Wikipedia fetch + parse helpers shared across category-specific sources.

Uses the public REST API (``en.wikipedia.org/api/rest_v1/page/html/<title>``)
which returns prerendered HTML — easier to parse than wikitext and stable.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
from bs4 import BeautifulSoup

WIKI_REST_HTML = "https://en.wikipedia.org/api/rest_v1/page/html/{title}"
USER_AGENT = "TechEngine-Coverage/0.1 (+https://github.com/GetTechAPI/TechEngine)"


def fetch_wikipedia_html(page_title: str, *, timeout: float = 30.0) -> str:
    """Download the parsed HTML for a Wikipedia page."""
    url = WIKI_REST_HTML.format(title=page_title)
    headers = {"User-Agent": USER_AGENT}
    with httpx.Client(headers=headers, timeout=timeout, follow_redirects=True) as client:
        response = client.get(url)
        response.raise_for_status()
        return response.text


def wikitable_first_cells(html: str) -> Iterator[str]:
    """Yield the text of the first cell of every row in every ``table.wikitable``.

    Most ``List_of_*_processors`` and ``List_of_*_graphics_processing_units``
    pages put the model name in column 1. Header rows whose first cell is a
    ``<th>`` are still emitted; the slug normalizer filters obvious non-models.
    """
    soup = BeautifulSoup(html, "html.parser")
    for table in soup.select("table.wikitable"):
        for row in table.select("tr"):
            cell = row.find(["th", "td"])
            if not cell:
                continue
            text = cell.get_text(" ", strip=True)
            if text:
                yield text
