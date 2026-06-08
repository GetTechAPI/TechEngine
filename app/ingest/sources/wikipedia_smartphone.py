"""Wikipedia smartphone list pages → ``IngestCandidate`` rows.

Each major OEM has a ``List of <brand> smartphones`` (or equivalent) page on
Wikipedia. We parse those, look up the SoC name against the curated TechAPI
SoC catalog, and emit candidates only for phones whose SoC is already
curated — the validator enforces ``smartphone.soc`` foreign-key integrity
so unknown SoCs would otherwise tank the whole dataset.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup

from app.coverage.curated import curated_slugs
from app.coverage.normalize import slugify
from app.coverage.sources.wikipedia import fetch_wikipedia_html

from ..normalize import (
    guess_os,
    parse_battery_mah,
    parse_date,
    parse_ram_gb,
    parse_weight_g,
)
from .base import IngestCandidate
from .wikitable import parse_table

PAGES: list[tuple[str, str]] = [
    ("samsung", "List_of_Samsung_Galaxy_smartphones"),
    ("apple", "List_of_iPhone_models"),
    ("google", "Pixel_(smartphone)"),
    ("oneplus", "List_of_OnePlus_products"),
    ("xiaomi", "List_of_Xiaomi_smartphones"),
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
            for row in parse_table(table, HEADER_RULES):
                model = row.cells.get("model", "")
                slug = slugify(model, manufacturer=brand)
                if len(slug) < 3:
                    continue
                yield _build_candidate(
                    brand=brand,
                    model=model,
                    slug=slug,
                    row=row.cells,
                    source_url=source_url,
                    known_socs=known_socs,
                )


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
    release_date = parse_date(row.get("release_date", ""))
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

    output_path = Path("smartphone") / brand / f"{slug}.json"

    return IngestCandidate(
        category="smartphone",
        manufacturer=brand,
        slug=slug,
        record=record,
        source_url=source_url,
        output_path=output_path,
        missing_fields=missing,
    )
