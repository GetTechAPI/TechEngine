"""Wikipedia CPU list pages → ``IngestCandidate`` rows.

Each ``List_of_<vendor>_<family>_processors`` page on Wikipedia is a series
of ``table.wikitable`` blocks; rows are individual SKUs and columns map to
schema fields. Column headers vary subtly between pages, so we match by
loose keywords (``"cores"``, ``"base"``, ``"tdp"`` …) rather than position.

Required output fields (per the validator): ``slug``, ``name``,
``manufacturer``, ``release_date``, ``segment``, ``architecture``,
``cores``, ``threads``. Anything missing collapses into
``IngestCandidate.missing_fields``; the pipeline skips drafts unless a
``--include-drafts`` flag is set.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.coverage.normalize import slugify
from app.coverage.sources.wikipedia import fetch_wikipedia_html

from ..normalize import (
    guess_cpu_segment,
    parse_cache_mb,
    parse_cores_threads,
    parse_date,
    parse_frequency_ghz,
    parse_int,
    parse_tdp_w,
)
from .base import IngestCandidate

# (manufacturer, page, architecture-fallback). Architecture is overridden
# per-table when a preceding ``<h2>``/``<h3>`` provides a better label.
PAGES: list[tuple[str, str, str]] = [
    ("intel", "List_of_Intel_Core_processors", "Intel Core"),
    ("intel", "List_of_Intel_Xeon_processors", "Intel Xeon"),
    ("amd", "List_of_AMD_Ryzen_processors", "AMD Ryzen"),
    ("amd", "List_of_AMD_Epyc_processors", "AMD EPYC"),
]

# Lowercased header tokens → canonical field name. Only the first match wins
# per row (so a "Cores/Threads" column maps to ``cores`` via the first hit).
HEADER_RULES: dict[str, list[str]] = {
    "model": ["model", "processor", "cpu", "name"],
    "cores": ["cores", "core"],
    "threads": ["threads", "thread"],
    "base_clock": ["base", "freq", "clock"],
    "boost_clock": ["boost", "turbo", "max"],
    "l3_cache": ["l3", "cache"],
    "tdp": ["tdp", "power", "wattage"],
    "release_date": ["released", "release", "launched", "launch", "date"],
    "socket": ["socket"],
    "process_node": ["process", "fab", "node", "lithography"],
}


class WikipediaCpuIngest:
    """Per-row ingestion from Wikipedia CPU list pages."""

    category = "cpu"
    name = "wikipedia-cpu-ingest"
    description = "Wikipedia: per-row extraction from List_of_*_processors pages."

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
            headers = _table_headers(table)
            if not headers or "model" not in headers.values():
                continue
            architecture = _nearest_section_label(table) or fallback_arch
            for row in table.select("tr"):
                cells = row.find_all(["td"])
                if not cells:
                    continue
                row_text = _row_by_field(cells, headers)
                model = row_text.get("model")
                if not model:
                    continue
                slug = slugify(model, manufacturer=manufacturer)
                if len(slug) < 4 or not any(ch.isdigit() for ch in slug):
                    continue
                yield _build_candidate(
                    manufacturer=manufacturer,
                    architecture=architecture,
                    model=model,
                    slug=slug,
                    row=row_text,
                    source_url=source_url,
                )


def _table_headers(table: Tag) -> dict[int, str]:
    """Map column index → canonical field name based on header text."""
    header_row = table.find("tr")
    if header_row is None:
        return {}
    out: dict[int, str] = {}
    index = 0
    for cell in header_row.find_all(["th", "td"]):
        if not isinstance(cell, Tag):
            continue
        text = cell.get_text(" ", strip=True).lower()
        canonical = _match_header(text)
        if canonical is not None:
            out[index] = canonical
        index += _colspan(cell)
    return out


def _match_header(text: str) -> str | None:
    for canonical, tokens in HEADER_RULES.items():
        for token in tokens:
            if token in text:
                return canonical
    return None


def _row_by_field(cells: list[Tag], headers: dict[int, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    index = 0
    for cell in cells:
        canonical = headers.get(index)
        if canonical is not None and canonical not in result:
            result[canonical] = cell.get_text(" ", strip=True)
        index += _colspan(cell)
    return result


def _colspan(cell: Tag) -> int:
    raw = cell.attrs.get("colspan")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if raw is None:
        return 1
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 1


def _nearest_section_label(table: Tag) -> str | None:
    for prev in table.find_all_previous(["h2", "h3", "h4"]):
        text = prev.get_text(" ", strip=True)
        if text and "edit" not in text.lower():
            # Wikipedia headings sometimes end with "[edit]" pre-strip.
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
    cores_field = row.get("cores", "")
    threads_field = row.get("threads", "")
    cores, threads_via_slash = parse_cores_threads(cores_field)
    threads = parse_int(threads_field) or threads_via_slash

    release_date = parse_date(row.get("release_date", ""))
    base_clock = parse_frequency_ghz(row.get("base_clock", ""))
    boost_clock = parse_frequency_ghz(row.get("boost_clock", ""))
    l3_cache = parse_cache_mb(row.get("l3_cache", ""))
    tdp = parse_tdp_w(row.get("tdp", ""))
    socket = row.get("socket") or None
    process_node = row.get("process_node") or None

    segment = guess_cpu_segment(model)
    name = model if model.lower().startswith(manufacturer) else f"{manufacturer.upper()} {model}"

    record: dict[str, object | None] = {
        "slug": slug,
        "name": name,
        "manufacturer": manufacturer,
        "release_date": release_date.isoformat() if release_date else None,
        "segment": segment,
        "architecture": architecture,
        "socket": socket,
        "process_node": process_node,
        "cores": cores,
        "threads": threads,
        "p_cores": None,
        "e_cores": None,
        "base_clock_ghz": base_clock,
        "boost_clock_ghz": boost_clock,
        "l3_cache_mb": l3_cache,
        "tdp_w": tdp,
        "max_tdp_w": None,
        "integrated_graphics": None,
        "memory_support": None,
        "cinebench_r23_single": None,
        "cinebench_r23_multi": None,
        "geekbench_single": None,
        "geekbench_multi": None,
        "msrp_usd": None,
        "verified": False,
        "source_urls": [source_url],
    }

    required = ("release_date", "architecture", "cores", "threads")
    missing = tuple(field for field in required if record.get(field) in (None, ""))

    year = release_date.year if release_date else "unknown"
    bucket = "enterprise" if segment in ("server", "hedt") else "consumer"
    output_path = Path("cpu") / manufacturer / str(year) / bucket / f"{slug}.json"

    return IngestCandidate(
        category="cpu",
        manufacturer=manufacturer,
        slug=slug,
        record=record,
        source_url=source_url,
        output_path=output_path,
        missing_fields=missing,
    )
