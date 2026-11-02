"""Wikipedia smartphone list pages → ``IngestCandidate`` rows.

Each major OEM has a ``List of <brand> smartphones`` (or equivalent) page on
Wikipedia. We parse those, look up the SoC name against the curated TechAPI
SoC catalog, and emit candidates only for phones whose SoC is already
curated — the validator enforces ``smartphone.soc`` foreign-key integrity
so unknown SoCs would otherwise tank the whole dataset.

A row is a phone only when it names one model and at least one spec parses.
Captions, year dividers, legends and the attribute rows of transposed
comparison tables name no phone; "iPhone 13 Pro / 13 Pro Max" names two.
Records go to ``smartphone/<brand>/<year>/<slug>.json``, so a row without a
release date is never written.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.coverage.curated import curated_slugs
from app.coverage.normalize import slugify
from app.coverage.sources.wikipedia import fetch_wikipedia_html

from ..normalize import (
    guess_os,
    parse_battery_mah,
    parse_date_period,
    parse_ram_gb,
    parse_weight_g,
    release_date_of,
)
from .base import IngestCandidate
from .wikitable import _detect_headers, _table_to_grid

PAGES: list[tuple[str, str]] = [
    ("samsung", "List_of_Samsung_Galaxy_smartphones"),
    ("apple", "List_of_iPhone_models"),
    ("google", "Pixel_(smartphone)"),
    ("oneplus", "List_of_OnePlus_products"),
]

# Brand keys are stored lowercase; these are their display forms used to
# synthesize ``name`` when the model string omits the brand. ``.title()`` is a
# fine default (samsung→Samsung) but mangles intercapped brands, so those get
# explicit entries (oneplus→OnePlus, not "Oneplus").
_BRAND_DISPLAY: dict[str, str] = {"oneplus": "OnePlus"}

HEADER_RULES: dict[str, list[str]] = {
    "model": ["model", "name"],
    "release_date": ["released", "release", "launched", "launch", "date"],
    "soc": ["soc", "chipset", "processor", "platform"],
    "ram": ["ram", "memory"],
    "battery": ["battery"],
    "weight": ["weight", "mass"],
    "os": ["os", "operating system", "software"],
    "display": ["display", "screen"],
    "camera": ["camera", "rear"],
}

# Vendor-name prefixes we trim before slugifying the SoC text. We keep the
# product family (Snapdragon / Dimensity / Exynos / Tensor / Kirin) and drop
# only the company name. Apple is the exception — "Apple A17 Pro" slugs as
# ``a17-pro`` because "Apple" is the family identifier, not a vendor prefix.
# Citation markers left in model cells ("Galaxy Ace 3 [ 35 ]").
_FOOTNOTE_RE = re.compile(r"\s*\[\s*[\w\s]{1,6}\]")
# A product word named twice means two phones in one cell ("iPhone 5 iPhone 5c").
_PRODUCT_WORD_RE = re.compile(r"\b(?:iphone|galaxy|pixel|oneplus|redmi|poco)\b", re.IGNORECASE)
# Without one of these the row is not a phone (a caption, a legend, a spec label).
_SPEC_FIELDS = ("soc", "release_date", "ram_gb", "battery_mah", "weight_g")

_SOC_VENDOR_PREFIXES = (
    "qualcomm",
    "mediatek",
    "samsung",
    "huawei",
    "google",
    "apple",
)


class WikipediaSmartphoneIngest:
    """Per-row ingestion from Wikipedia per-brand smartphone list pages."""

    category = "smartphone"
    name = "wikipedia-smartphone-ingest"
    description = "Wikipedia: per-row extraction from brand-specific smartphone list pages."

    def __init__(self, pages: list[tuple[str, str]] | None = None) -> None:
        self._pages = pages if pages is not None else PAGES
        # Lazy: populated on first ``fetch()`` call so tests can monkeypatch
        # TECHAPI_DATA_DIR before the source touches disk.
        self._known_socs: set[str] | None = None
        self._known_brands: set[str] | None = None

    def fetch(self, *, limit: int | None = None) -> Iterator[IngestCandidate]:
        self._refresh_curated_indexes()
        emitted = 0
        for brand, page in self._pages:
            if self._known_brands is not None and brand not in self._known_brands:
                continue
            try:
                html = fetch_wikipedia_html(page)
            except Exception:
                continue
            for candidate in self._extract(html, brand, page, self._known_socs or set()):
                yield candidate
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

    def _refresh_curated_indexes(self) -> None:
        self._known_socs = curated_slugs("soc")
        self._known_brands = curated_slugs("brand")

    @staticmethod
    def _extract(
        html: str, brand: str, page: str, known_socs: set[str]
    ) -> Iterator[IngestCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        source_url = f"https://en.wikipedia.org/wiki/{page}"
        for table in soup.select("table.wikitable"):
            for cells in _rows(table):
                model = _FOOTNOTE_RE.sub("", cells.get("model", "")).strip()
                if "/" in model or len(_PRODUCT_WORD_RE.findall(model)) > 1:
                    continue  # two phones in one cell; splitting would guess the names
                slug = slugify(model, manufacturer=brand)
                if len(slug) < 3:
                    continue
                candidate = _build_candidate(
                    brand=brand,
                    model=model,
                    slug=slug,
                    row=cells,
                    source_url=source_url,
                    known_socs=known_socs,
                )
                if any(candidate.record.get(field) is not None for field in _SPEC_FIELDS):
                    yield candidate


def _rows(table: Tag) -> Iterator[dict[str, str]]:
    """Body rows of ``table`` keyed by field, the way ``wikitable.parse_table``
    reads them, and also through a two-row header: under "Release(d)" spanning
    "With OS" and "Date", the release date is the "Date" column, and the
    second header row is not a phone.
    """
    grid = _table_to_grid(table)
    detected = _detect_headers(grid, HEADER_RULES) if grid else None
    if detected is None:
        return
    header, mapping = detected
    model_column = min(column for column, field in mapping.items() if field == "model")
    start = header + 1
    if (
        start < len(grid)
        and model_column < len(grid[start])
        and grid[start][model_column] == grid[header][model_column]
    ):
        leaves = grid[start]
        dates = {
            column
            for column, field in mapping.items()
            if field == "release_date" and column < len(leaves) and "date" in leaves[column].lower()
        }
        if dates:
            mapping = {
                column: field
                for column, field in mapping.items()
                if field != "release_date" or column in dates
            }
        start += 1
    for row in grid[start:]:
        cells: dict[str, str] = {}
        for column, text in enumerate(row):
            field = mapping.get(column)
            if field and text:
                cells.setdefault(field, text)
        if cells:
            yield cells


def soc_text_to_slug(text: str) -> str:
    """Heuristically map an SoC table cell to a TechAPI SoC slug.

    The shape of the text varies wildly — ``"Snapdragon 8 Gen 3"``,
    ``"Qualcomm Snapdragon 8 Gen 3 Mobile Platform"``,
    ``"Apple A17 Pro"`` — so we strip vendor prefixes, drop common
    promotional suffixes, then run the standard slugifier.
    """
    if not text:
        return ""
    # Wikipedia often appends " for Galaxy" / " (Mobile Platform)" etc.
    cleaned = re.sub(r"\s+for\s+\w+\b", "", text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+\(.+?\)\s*$", "", cleaned)
    cleaned = re.sub(r"\s+mobile\s+platform\b", "", cleaned, flags=re.IGNORECASE)
    lowered = cleaned.strip().lower()
    for prefix in _SOC_VENDOR_PREFIXES:
        if lowered.startswith(prefix + " "):
            cleaned = cleaned[len(prefix) + 1 :]
            break
    return slugify(cleaned)


def _build_candidate(
    *,
    brand: str,
    model: str,
    slug: str,
    row: dict[str, str],
    source_url: str,
    known_socs: set[str],
) -> IngestCandidate:
    release_period = parse_date_period(row.get("release_date", ""))
    release_date = release_date_of(release_period)
    soc_text = row.get("soc", "")
    soc_slug = soc_text_to_slug(soc_text)
    soc_value = soc_slug if soc_slug in known_socs else None

    ram_gb = parse_ram_gb(row.get("ram", ""))
    battery = parse_battery_mah(row.get("battery", ""))
    weight = parse_weight_g(row.get("weight", ""))
    os_value = guess_os(row.get("os", ""), brand=brand)

    name = (
        model
        if model.lower().startswith(brand) or model.lower().startswith(brand.upper())
        else f"{_BRAND_DISPLAY.get(brand, brand.title())} {model}"
    )

    record: dict[str, object | None] = {
        "slug": slug,
        "name": name,
        "brand": brand,
        "soc": soc_value,
        "release_date": release_date.isoformat() if release_date else None,
        "ram_gb": ram_gb,
        "battery_mah": battery,
        "weight_g": weight,
        "os": os_value,
        "msrp_usd": None,
        "verified": False,
        "source_urls": [source_url],
    }

    required = ("soc", "release_date", "ram_gb", "battery_mah", "weight_g", "os")
    missing = tuple(field for field in required if record.get(field) in (None, ""))

    year = release_date.year if release_date else "unknown"
    output_path = Path("smartphone") / brand / str(year) / f"{slug}.json"
    # The layout files a phone under its release year; without one there is no
    # folder to put it in, even as a draft.
    problems = () if release_date else ("no release date to file it under",)

    return IngestCandidate(
        category="smartphone",
        manufacturer=brand,
        slug=slug,
        record=record,
        source_url=source_url,
        output_path=output_path,
        missing_fields=missing,
        problems=problems,
        released_by=release_period[1] if release_period else None,
    )
