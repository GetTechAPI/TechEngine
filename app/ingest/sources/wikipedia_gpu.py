"""Wikipedia GPU list pages → ``IngestCandidate`` rows.

The ``List_of_<vendor>_graphics_processing_units`` pages share the same
shape as the CPU lists (multiple ``wikitable``s, header-keyed columns) so
this source reuses the shared grid parser. Required GPU schema fields are
stricter than CPU: ``memory_gb``, ``memory_type``, ``memory_bus_bit``,
``base_clock_mhz``, ``boost_clock_mhz``, ``tdp_w``, ``pcie_version``. Many
list-page rows leave several of those blank — those rows surface as
incomplete candidates and stay out of the PR unless ``--include-drafts``.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.coverage.normalize import slugify
from app.coverage.sources.wikipedia import fetch_wikipedia_html

from ..normalize import (
    guess_gpu_segment,
    parse_date,
    parse_frequency_mhz,
    parse_memory_bus_bit,
    parse_memory_gb,
    parse_pcie_version,
    parse_tdp_w,
)
from .base import IngestCandidate
from .wikitable import parse_table

PAGES: list[tuple[str, str, str]] = [
    ("nvidia", "List_of_Nvidia_graphics_processing_units", "NVIDIA GeForce"),
    ("amd", "List_of_AMD_graphics_processing_units", "AMD Radeon"),
    ("intel", "List_of_Intel_graphics_processing_units", "Intel Graphics"),
]

# Manufacturer keys are stored lowercase; these are their display forms used to
# synthesize ``name`` when the model string omits the brand. Plain ``.upper()``
# mangles "intel" → "INTEL" (an ingest casing artifact). NVIDIA and AMD are
# genuinely all-caps so they get explicit entries rather than title-casing.
_BRAND_DISPLAY: dict[str, str] = {"nvidia": "NVIDIA", "amd": "AMD", "intel": "Intel"}

# Same matching strategy as CPU but with GPU-specific keyword sets.
HEADER_RULES: dict[str, list[str]] = {
    "model": ["model", "card", "name"],
    "architecture": ["architecture", "codename", "code name", "chip"],
    "release_date": ["released", "release", "launched", "launch", "date"],
    "memory": ["memory", "vram"],
    "memory_type": ["memory type", "mem type", "type"],
    "memory_bus": ["bus", "interface"],
    "base_clock": ["base", "core clock"],
    "boost_clock": ["boost", "turbo", "max"],
    "tdp": ["tdp", "tbp", "power"],
    "process_node": ["process", "fab", "lithography"],
    "pcie": ["pcie", "pci-e", "pci express"],
}


class WikipediaGpuIngest:
    """Per-row ingestion from Wikipedia GPU list pages."""

    category = "gpu"
    name = "wikipedia-gpu-ingest"
    description = "Wikipedia: per-row extraction from List_of_*_graphics_processing_units pages."

    def __init__(self, pages: list[tuple[str, str, str]] | None = None) -> None:
        self._pages = pages if pages is not None else PAGES

    def fetch(self, *, limit: int | None = None) -> Iterator[IngestCandidate]:
        emitted = 0
        for manufacturer, page, fallback_arch in self._pages:
            try:
                html = fetch_wikipedia_html(page)
            except Exception:
                continue
            for candidate in self._extract(html, manufacturer, page, fallback_arch):
                yield candidate
                emitted += 1
                if limit is not None and emitted >= limit:
                    return

    @staticmethod
    def _extract(
        html: str, manufacturer: str, page: str, fallback_arch: str
    ) -> Iterator[IngestCandidate]:
        soup = BeautifulSoup(html, "html.parser")
        source_url = f"https://en.wikipedia.org/wiki/{page}"
        for table in soup.select("table.wikitable"):
            section_label = _nearest_section_label(table) or fallback_arch
            for row in parse_table(table, HEADER_RULES):
                model = row.cells.get("model", "")
                slug = slugify(model, manufacturer=manufacturer)
                if len(slug) < 4 or not any(ch.isdigit() for ch in slug):
                    continue
                architecture = row.cells.get("architecture") or section_label
                yield _build_candidate(
                    manufacturer=manufacturer,
                    architecture=architecture,
                    model=model,
                    slug=slug,
                    row=row.cells,
                    source_url=source_url,
                )


def _nearest_section_label(table: Tag) -> str | None:
    for prev in table.find_all_previous(["h2", "h3", "h4"]):
        text = prev.get_text(" ", strip=True)
        if text and "edit" not in text.lower():
            return text.split("[")[0].strip() or None
    return None


def _build_candidate(
    *,
    manufacturer: str,
    architecture: str,
    model: str,
    slug: str,
    row: dict[str, str],
    source_url: str,
) -> IngestCandidate:
    release_date = parse_date(row.get("release_date", ""))
    memory_gb = parse_memory_gb(row.get("memory", ""))
    memory_bus_bit = parse_memory_bus_bit(row.get("memory_bus", ""))
    base_clock_mhz = parse_frequency_mhz(row.get("base_clock", ""))
    boost_clock_mhz = parse_frequency_mhz(row.get("boost_clock", ""))
    tdp_w = parse_tdp_w(row.get("tdp", ""))
    pcie_version = parse_pcie_version(row.get("pcie", ""))
    memory_type = (row.get("memory_type") or "").upper() or None

    segment = guess_gpu_segment(model)
    brand = _BRAND_DISPLAY.get(manufacturer, manufacturer.title())
    name = model if model.lower().startswith(manufacturer) else f"{brand} {model}"

    record: dict[str, object | None] = {
        "slug": slug,
        "name": name,
        "manufacturer": manufacturer,
        "architecture": architecture,
        "release_date": release_date.isoformat() if release_date else None,
        "memory_gb": memory_gb,
        "memory_type": memory_type,
        "memory_bus_bit": memory_bus_bit,
        "base_clock_mhz": base_clock_mhz,
        "boost_clock_mhz": boost_clock_mhz,
        "tdp_w": tdp_w,
        "pcie_version": pcie_version,
        "msrp_usd": None,
        "verified": False,
        "source_urls": [source_url],
    }

    required = (
        "architecture",
        "release_date",
        "memory_gb",
        "memory_type",
        "memory_bus_bit",
        "base_clock_mhz",
        "boost_clock_mhz",
        "tdp_w",
        "pcie_version",
    )
    missing = tuple(field for field in required if record.get(field) in (None, ""))

    year = release_date.year if release_date else "unknown"
    output_path = Path("gpu") / manufacturer / str(year) / segment / f"{slug}.json"

    return IngestCandidate(
        category="gpu",
        manufacturer=manufacturer,
        slug=slug,
        record=record,
        source_url=source_url,
        output_path=output_path,
        missing_fields=missing,
    )
