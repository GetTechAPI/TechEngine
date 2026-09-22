"""Backfill Wikipedia URLs onto kaggle-only GPU records.

Targets TechAPI GPU seeds whose only ``source_urls`` entry is the TechPowerUp
dump published as the Kaggle dataset ``ellimaaac/gpus-specs-from-1986-to-2026``.
``techpowerup.com`` is already Tier-1, but Cloudflare blocks a live scrape, so
this tool cross-references ``en.wikipedia.org`` (also Tier-1) instead.

The gate reuses existing verification pieces:

* list pages come from :data:`app.ingest.sources.wikipedia_gpu.PAGES` (plus a
  few legacy articles that are sections, not ingest tables)
* the card name is split so memory (``4 MB``) and bus (``PCI`` / ``AGP`` /
  ``PCIe``) suffixes do not block :func:`app.verify.crossref._heading_matches`
* those stripped values must agree with the record's own ``memory_gb`` /
  ``pcie_version`` — they are a consistency gate, not a confirm by themselves
* a confirm needs two agreeing specs and a spec rank of at least 2, so a
  launch year alone cannot confirm
* a desktop/mobile marker (Mobility, Mobile, Go, Max-Q, or an ``M`` suffix)
  on only the record or only the Wikipedia row/section drops that row
* a section bus (``AGP`` vs ``PCIe``) that disagrees with ``pcie_version``
  is a conflict
* two same-named rows that still disagree (``RV370`` vs ``RV380``, or two
  sections) stay ambiguous instead of taking the first
* liveness is :func:`app.verify.http_check.classify`
* the resume cache is append-only JSONL via :func:`app.verify.ledger.iter_entries`

``--dry-run`` (the default) never writes a TechAPI file. ``--apply`` appends
the Wikipedia URL to ``source_urls`` for CONFIRM rows only.

::

    python -m app.verify.wikipedia_gpu_backfill \\
        --data-root C:/path/to/TechAPI --limit 200 --sleep 1.5 --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.ingest.normalize import (
    parse_date,
    parse_frequency_mhz,
    parse_memory_bus_bit,
    parse_tdp_w,
)
from app.ingest.sources.wikipedia_gpu import PAGES as INGEST_PAGES
from app.ingest.sources.wikitable import parse_table
from app.verify import ledger
from app.verify.crossref import (
    AMBIGUOUS,
    CONFIRM,
    CONTRADICT,
    NOTFOUND,
    Candidate,
    WikipediaFetcher,
    _heading_matches,
    normalize_heading,
)
from app.verify.http_check import classify

KAGGLE_GPU_URL = "https://www.kaggle.com/datasets/ellimaaac/gpus-specs-from-1986-to-2026"
WIKI_REST_HTML = "https://en.wikipedia.org/api/rest_v1/page/html/{title}"
USER_AGENT = (
    "TechEngine-verify/0.1 (https://github.com/GetTechAPI/TechEngine; gpu wikipedia crossref)"
)
DECISIONS = (CONFIRM, AMBIGUOUS, NOTFOUND, CONTRADICT)
MIN_SLEEP_S = 1.0
# Bump when the gate changes so a resume cannot replay a stale CONFIRM.
GATE_VERSION = 2
# Rank 1 is launch year or a section bus hint. CONFIRM needs a stronger spec
# and at least two agreeing fields (year + memory counts; year alone does not).
MIN_CONFIRM_RANK = 2
MIN_CONFIRM_AGREEMENTS = 2
# Articles whose products are section headings rather than ingest-shaped tables.
# Verified live: ``3dfx`` has a Voodoo Banshee section; ``RIVA_128`` is its own
# article. They are cross-reference sources only — not added to ingest PAGES.
LEGACY_PAGES: tuple[tuple[str, str, str], ...] = (
    ("3dfx", "3dfx", "3dfx"),
    ("nvidia", "RIVA_128", "RIVA 128"),
)
# Manufacturer display prefixes Wikipedia often drops. Folding only these still
# counts as an exact heading, not a different SKU (``Ultra`` / ``LE`` / ``Ti``).
_BRAND_TOKENS = ("nvidia", "amd", "ati", "intel", "3dfx", "s3")
_MEMORY_SUFFIX_RE = re.compile(
    r"(?<![A-Za-z])(\d+(?:\.\d+)?)\s*(KB|MB|GB)\s*$",
    re.IGNORECASE,
)
_INTERFACE_SUFFIX_RE = re.compile(
    r"\s+(PCI(?:\s*Express|-E|e)?|AGP|MXM)(?:\s+Pro)?(?:\s+x\s*\d+|\s+\d+\s*x)?\s*$",
    re.IGNORECASE,
)
_MEMORY_TOKEN_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(KB|MB|GB)\b", re.IGNORECASE)
_SHARED_MEMORY_RE = re.compile(
    r"((?:\d+(?:\.\d+)?\s*/\s*)+\d+(?:\.\d+)?)\s*(KB|MB|GB)\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_TDP_PROSE_RE = re.compile(r"\b(\d{1,3})\s*W\b")
_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")
_SKIP_HEADING_RE = re.compile(
    r"\b(series|references|see also|external links|notes|history|overview|features|"
    r"support|driver|comparison|contents|field explanations)\b",
    re.IGNORECASE,
)
_PRODUCT_TOKEN_RE = re.compile(
    r"\b(voodoo|banshee|riva|rage|quadro|geforce|radeon|firegl|firepro|arc|chrome|"
    r"velocity|nvs|tesla)\b",
    re.IGNORECASE,
)
_PCIE_RE = re.compile(r"pci[-\s]?(?:e|express)\b", re.IGNORECASE)
# Desktop vs laptop lines share a number. A marker on only one side is a
# different product (Radeon 9600 vs Mobility Radeon 9600, 6330 vs 6330M).
_FORM_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mobility", re.compile(r"\bmobility\b", re.IGNORECASE)),
    ("mobile", re.compile(r"\bmobile\b|모바일", re.IGNORECASE)),
    ("go", re.compile(r"\bgo\b", re.IGNORECASE)),
    ("max-q", re.compile(r"\bmax\s*q\b", re.IGNORECASE)),
    ("m-suffix", re.compile(r"(?<![A-Za-z])\d+\s*m\b", re.IGNORECASE)),
)

# First matching canonical field wins. Specific columns are listed before the
# bare "memory" / "clock" / "power" fragments so bandwidth and memory-clock
# columns are not read as capacity.
ROW_HEADER_RULES: dict[str, list[str]] = {
    "memory_clock": ["memory clock"],
    "memory_bandwidth": ["memory bandwidth", "bandwidth"],
    "memory_type": ["memory type", "mem type"],
    "memory": ["memory size", "vram", "memory"],
    "memory_bus": ["bus width", "memory bus", "bus interface"],
    "base_clock": ["core clock", "coreclock", "base clock", "gpu clock", "clock"],
    "tdp": ["tdp", "tbp", "power max", "power"],
    "release_date": ["launch", "released", "release"],
    "interface": ["interface"],
    "model": ["model", "product", "card"],
}

FetchPage = Callable[[str], tuple[int | None, str, str]]
SearchFn = Callable[[str], list[Candidate]]


@dataclass(frozen=True)
class CardName:
    """Record name with trailing memory and bus suffixes removed."""

    original: str
    base: str
    memory_gb: float | None
    interface: str | None


@dataclass(frozen=True)
class WikiRow:
    model: str
    url: str
    page: str
    section: str | None = None
    memory_gb: tuple[float, ...] = ()
    tdp_w: int | None = None
    year: int | None = None
    bus_bit: int | None = None
    base_clock_mhz: int | None = None
    interfaces: frozenset[str] = frozenset()
    # Bus named only by the section heading ("AGP", "PCIe"). A mismatch with
    # the record is a conflict; the cell interface wins when the row has one.
    section_interface: str | None = None


@dataclass
class GateResult:
    decision: str
    proposed_url: str | None
    inspected_url: str | None
    title: str | None
    liveness: str | None
    agreements: list[str]
    conflicts: list[str]
    reason: str
    suffix_only: bool
    base_name: str


@dataclass
class RunResult:
    rows: list[dict[str, Any]] = field(default_factory=list)
    brands: set[str] = field(default_factory=set)
    cached: int = 0
    requests: int = 0
    index_rows: int = 0
    index_pages: dict[str, int] = field(default_factory=dict)
    eligible: int = 0
    stopped: str | None = None
    written: int = 0
    skipped_writes: int = 0

    def counts(self) -> dict[str, int]:
        totals = {name: 0 for name in DECISIONS}
        for row in self.rows:
            decision = row.get("decision")
            if isinstance(decision, str) and decision in totals:
                totals[decision] += 1
        return totals


def crossref_pages() -> list[tuple[str, str, str]]:
    """Ingest list pages, then legacy articles that are not ingest targets."""
    seen: set[str] = set()
    pages: list[tuple[str, str, str]] = []
    for manufacturer, page, fallback in [*INGEST_PAGES, *LEGACY_PAGES]:
        if page in seen:
            continue
        seen.add(page)
        pages.append((manufacturer, page, fallback))
    return pages


# --- name split -----------------------------------------------------------------


def _to_gb(value: float, unit: str) -> float:
    unit_l = unit.lower()
    if unit_l == "gb":
        return value
    if unit_l == "mb":
        return round(value / 1024, 3)
    return round(value / (1024 * 1024), 6)


def memory_close(left: float, right: float) -> bool:
    """True when two GiB-ish sizes are the same module size (4 MB vs 0.004)."""
    left_mb = left * 1024
    right_mb = right * 1024
    return abs(left_mb - right_mb) <= max(1.5, 0.04 * max(left_mb, right_mb))


def interface_families(text: str) -> frozenset[str]:
    """Bus families named in ``text``. ``PCIe`` does not also count as ``PCI``."""
    if not text:
        return frozenset()
    found: set[str] = set()
    if _PCIE_RE.search(text):
        found.add("pcie")
    if re.search(r"\bagp\b", text, re.IGNORECASE):
        found.add("agp")
    stripped = _PCIE_RE.sub(" ", text)
    if re.search(r"\bpci\b", stripped, re.IGNORECASE):
        found.add("pci")
    if re.search(r"\bmxm\b", text, re.IGNORECASE):
        found.add("mxm")
    return frozenset(found)


def split_card_name(name: str) -> CardName:
    """Strip trailing memory and bus suffixes so the base name can heading-match.

    ``Voodoo Banshee AGP 16 MB`` → base ``Voodoo Banshee``, 0.016 GB, ``agp``.
    ``Quadro4 100 NVS PCI`` → base ``Quadro4 100 NVS``, interface ``pci``.
    A trailing ``PCIe x1`` is one suffix. Tokens that are not at the end stay.
    """
    base = re.sub(r"\s+", " ", name).strip()
    memory: float | None = None
    interface: str | None = None
    while base:
        mem = _MEMORY_SUFFIX_RE.search(base)
        if mem:
            if memory is None:
                memory = _to_gb(float(mem.group(1)), mem.group(2))
            base = base[: mem.start()].strip()
            continue
        iface = _INTERFACE_SUFFIX_RE.search(base)
        if iface:
            if interface is None:
                families = interface_families(iface.group(0))
                interface = next(iter(families), None)
            base = base[: iface.start()].strip()
            continue
        break
    return CardName(name, base, memory, interface)


def brand_prefix_equal(left: str, right: str) -> bool:
    """Exact heading, or the same heading plus a manufacturer token."""
    a, b = normalize_heading(left), normalize_heading(right)
    if not a or not b:
        return False
    if a == b:
        return True
    return any(a == brand + b or b == brand + a for brand in _BRAND_TOKENS)


def parse_memory_options(text: str) -> tuple[float, ...]:
    """Every capacity in ``text``, including a shared unit (``128/256 MB``)."""
    if not text:
        return ()
    found: list[float] = []
    consumed: list[tuple[int, int]] = []
    for match in _SHARED_MEMORY_RE.finditer(text):
        consumed.append(match.span())
        unit = match.group(2)
        for piece in match.group(1).split("/"):
            piece = piece.strip()
            if not piece:
                continue
            value = _to_gb(float(piece), unit)
            if value not in found:
                found.append(value)
    for match in _MEMORY_TOKEN_RE.finditer(text):
        if any(start <= match.start() < end for start, end in consumed):
            continue
        value = _to_gb(float(match.group(1)), match.group(2))
        if value not in found:
            found.append(value)
    return tuple(found)


# --- Wikipedia rows --------------------------------------------------------------


def _clean_model(text: str) -> str:
    text = _FOOTNOTE_RE.sub("", text.replace("\xa0", " "))
    return re.sub(r"\s+", " ", text).strip(" -")


_PAREN_RE = re.compile(r"\([^)]*\)")


def comparable_title(model: str) -> str:
    """Card name with parenthetical codenames removed (``HD 6450 (Caicos)``)."""
    return re.sub(r"\s+", " ", _PAREN_RE.sub(" ", model)).strip()


def form_factor_marks(name: str) -> frozenset[str]:
    """Desktop/mobile markers in a record name or a Wikipedia title/section/URL.

    Underscores and hyphens are spaces so an anchor like ``Mobility_Radeon``
    or ``6000M_series`` still counts. ``128 MB`` and ``MX150`` do not.
    """
    text = comparable_title(unquote(name)).replace("_", " ").replace("-", " ")
    return frozenset(label for label, pattern in _FORM_RULES if pattern.search(text))


def form_factor_conflict(left: str, right: str) -> bool:
    """True when a Mobility/Mobile/Go/Max-Q/M-suffix marker is on only one side."""
    return form_factor_marks(left) != form_factor_marks(right)


def row_form_text(row: WikiRow) -> str:
    """Model plus the section title and section URL the marker gate reads."""
    return " ".join(part for part in (row.model, row.section, row.url) if part)


def _plausible_table_model(model: str) -> bool:
    if not (3 <= len(model) <= 80):
        return False
    if not re.search(r"[A-Za-z]", model) or not re.search(r"\d", model):
        return False
    lowered = model.lower()
    if lowered in {"model", "notes", "card"} or re.search(r"\bbased\b", lowered):
        return False
    return True


def _usable_section_heading(model: str) -> bool:
    if not (3 <= len(model) <= 80) or _SKIP_HEADING_RE.search(model):
        return False
    if not re.search(r"[A-Za-z]", model):
        return False
    return bool(re.search(r"\d", model) or _PRODUCT_TOKEN_RE.search(model))


def _year_of_cell(text: str) -> int | None:
    parsed = parse_date(text)
    return parsed.year if parsed is not None else None


def _nearest_section_label(table: Tag) -> str | None:
    for prev in table.find_all_previous(["h2", "h3", "h4"]):
        text = _clean_model(prev.get_text(" ", strip=True))
        if text and "edit" not in text.lower():
            return text.split("[")[0].strip() or None
    return None


def _article_url(final_url: str, page: str) -> str:
    marker = "/page/html/"
    if marker in final_url:
        slug = unquote(final_url.split(marker, 1)[1].split("?", 1)[0])
        return f"https://en.wikipedia.org/wiki/{quote(slug, safe='/:')}"
    return f"https://en.wikipedia.org/wiki/{quote(page, safe='/:')}"


def _section_url(page_url: str, section: str | None) -> str:
    if not section:
        return page_url
    anchor = quote(section.replace(" ", "_"), safe="_()'")
    return page_url.split("#", 1)[0] + "#" + anchor


def _bare_memory(text: str, unit: str) -> tuple[float, ...]:
    """``512`` under a ``Size (MB)`` subheader → 0.5 GB. Cells with words are left alone."""
    if re.search(r"[A-Za-z]", text):
        return ()
    numbers = re.findall(r"\d+(?:\.\d+)?", text)
    if not numbers or len(numbers) > 4:
        return ()
    found: list[float] = []
    for number in numbers:
        value = _to_gb(float(number), unit)
        if value not in found:
            found.append(value)
    return tuple(found)


def table_memory_unit(table: Tag) -> str | None:
    """Unit for a bare memory-size cell, taken from the header or its subheader.

    List tables often put ``512`` under ``Memory`` / ``Size (MB)`` and spell the
    unit only in the header. Bandwidth columns (``GB/s``) are ignored.
    """
    unit: str | None = None
    for row in table.select("tr")[:2]:
        for cell in row.find_all(["th", "td"]):
            text = cell.get_text(" ", strip=True).lower()
            if "clock" in text or "bandwidth" in text:
                continue
            if not any(token in text for token in ("memory", "vram", "size")):
                continue
            if re.search(r"\b(mib|mb)\b", text):
                return "mb"
            if re.search(r"\b(gib|gb)\b", text):
                unit = "gb"
    return unit


def _row_from_cells(
    *,
    model: str,
    cells: dict[str, str],
    page: str,
    page_url: str,
    section: str | None,
    memory_unit: str | None = None,
) -> WikiRow | None:
    model = _clean_model(model)
    if not model:
        return None
    memory_text = cells.get("memory", "")
    memory_gb = parse_memory_options(memory_text)
    if not memory_gb and memory_unit:
        memory_gb = _bare_memory(memory_text, memory_unit)
    bus_text = cells.get("memory_bus", "")
    iface_text = " ".join(part for part in (cells.get("interface", ""), bus_text) if part)
    families = interface_families(iface_text)
    section_iface = interface_families(section or "")
    section_one = next(iter(section_iface)) if len(section_iface) == 1 else None
    year = _year_of_cell(cells.get("release_date", ""))
    tdp = parse_tdp_w(cells.get("tdp", ""))
    clock = parse_frequency_mhz(cells.get("base_clock", ""))
    bus = parse_memory_bus_bit(bus_text)
    return WikiRow(
        model=model,
        url=_section_url(page_url, section),
        page=page,
        section=section,
        memory_gb=memory_gb,
        tdp_w=tdp,
        year=year,
        bus_bit=bus,
        base_clock_mhz=clock,
        interfaces=families,
        section_interface=section_one if not families else None,
    )


def _prose_specs(text: str) -> tuple[int | None, tuple[float, ...], int | None]:
    """Year / single memory / single TDP from a section lead. Ambiguous prose is dropped."""
    years = [int(match) for match in _YEAR_RE.findall(text)]
    year = years[0] if years and max(years) - min(years) <= 1 else None
    memories = parse_memory_options(text)
    memory = memories if len(memories) == 1 else ()
    tdps = [int(match) for match in _TDP_PROSE_RE.findall(text)]
    tdp = tdps[0] if len(set(tdps)) == 1 else None
    return year, memory, tdp


def _first_paragraph(heading: Tag) -> str:
    for sib in heading.next_siblings:
        if isinstance(sib, Tag) and sib.name in {"h2", "h3", "h4"}:
            break
        if isinstance(sib, Tag) and sib.name == "p":
            return sib.get_text(" ", strip=True)
    return ""


def _infobox_cells(soup: BeautifulSoup) -> dict[str, str]:
    box = soup.select_one("table.infobox")
    if box is None:
        return {}
    cells: dict[str, str] = {}
    for row in box.select("tr"):
        header = row.find("th")
        value = row.find("td")
        if not isinstance(header, Tag) or not isinstance(value, Tag):
            continue
        label = header.get_text(" ", strip=True).lower()
        text = value.get_text(" ", strip=True)
        if not label or not text:
            continue
        if "release" in label or label.startswith("launch") or "introduced" in label:
            cells.setdefault("release_date", text)
        elif "memory size" in label or label.strip() in {"memory", "vram"}:
            cells.setdefault("memory", text)
        elif "tdp" in label or "tbp" in label or label.startswith("power"):
            cells.setdefault("tdp", text)
        elif "bus" in label and "memory" in label:
            cells.setdefault("memory_bus", text)
        elif "core clock" in label or label.strip() == "clock":
            cells.setdefault("base_clock", text)
        elif "interface" in label or "bus type" in label:
            cells.setdefault("interface", text)
    return cells


def rows_from_html(html: str, page: str, page_url: str | None = None) -> list[WikiRow]:
    """Table rows, product section leads, and a title infobox when the page is a card."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    url = page_url or f"https://en.wikipedia.org/wiki/{quote(page, safe='/:')}"
    out: list[WikiRow] = []
    for table in soup.select("table.wikitable"):
        section = _nearest_section_label(table)
        memory_unit = table_memory_unit(table)
        for grid in parse_table(table, ROW_HEADER_RULES):
            model = grid.cells.get("model", "")
            if not _plausible_table_model(_clean_model(model)):
                continue
            row = _row_from_cells(
                model=model,
                cells=grid.cells,
                page=page,
                page_url=url,
                section=section,
                memory_unit=memory_unit,
            )
            if row is not None:
                out.append(row)
    for heading in soup.select("h2, h3, h4"):
        title = _clean_model(heading.get_text(" ", strip=True))
        if not _usable_section_heading(title):
            continue
        year, memory, tdp = _prose_specs(_first_paragraph(heading))
        if year is None and not memory and tdp is None:
            continue
        out.append(
            WikiRow(
                model=title,
                url=_section_url(url, title),
                page=page,
                section=title,
                memory_gb=memory,
                tdp_w=tdp,
                year=year,
            )
        )
    title_node = soup.find("title")
    page_title = _clean_model(title_node.get_text(" ", strip=True) if title_node else page)
    page_title = re.sub(r"\s*-\s*Wikipedia\s*$", "", page_title, flags=re.IGNORECASE).strip()
    if page_title and "list of" not in page_title.lower() and _usable_section_heading(page_title):
        info = _infobox_cells(soup)
        if info:
            row = _row_from_cells(
                model=page_title, cells=info, page=page, page_url=url, section=None
            )
            if row is not None:
                out.append(row)
    return out


class WikipediaListFetcher:
    """:class:`app.verify.crossref.Fetcher` over parsed Wikipedia rows.

    ``search`` returns heading matches for the base card name. Spec fields stay
    on :class:`WikiRow`; the decision reads those rows, not just the titles.
    """

    def __init__(self, rows: Iterable[WikiRow]) -> None:
        self.rows = list(rows)

    def search(self, name: str) -> list[Candidate]:
        base = split_card_name(name).base
        hits = [
            row for row in self.rows if base and _heading_matches(base, comparable_title(row.model))
        ]
        return [Candidate(title=row.model, url=row.url, year=row.year) for row in hits]

    def rows_for(self, name: str) -> list[WikiRow]:
        base = split_card_name(name).base
        if not base:
            return []
        return [row for row in self.rows if _heading_matches(base, comparable_title(row.model))]


# --- spec gate -------------------------------------------------------------------


def _positive(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    if number <= 0:
        return None
    return number


def _record_year(record: dict[str, Any]) -> int | None:
    raw = record.get("release_date")
    if isinstance(raw, str) and len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    return None


def compare_specs(
    record: dict[str, Any], row: WikiRow, card: CardName
) -> tuple[list[str], list[str]]:
    """Return ``(agreements, conflicts)`` between a record and one Wikipedia row.

    Name suffixes are checked against the record. A mismatch is a conflict, and
    a match is not an agreement: confirming still needs a Wikipedia spec.
    """
    agreements: list[str] = []
    conflicts: list[str] = []
    rec_mem = _positive(record.get("memory_gb"))
    if card.memory_gb is not None and rec_mem is not None:
        if not memory_close(card.memory_gb, rec_mem):
            conflicts.append("name_memory_gb")
    rec_iface = interface_families(str(record.get("pcie_version") or ""))
    if card.interface and rec_iface and card.interface not in rec_iface:
        conflicts.append("name_interface")

    if row.memory_gb and rec_mem is not None:
        if any(memory_close(rec_mem, option) for option in row.memory_gb):
            agreements.append("memory_gb")
        else:
            conflicts.append("memory_gb")
    rec_bus = record.get("memory_bus_bit")
    if row.bus_bit is not None and isinstance(rec_bus, int) and not isinstance(rec_bus, bool):
        if rec_bus == row.bus_bit:
            agreements.append("memory_bus_bit")
        else:
            conflicts.append("memory_bus_bit")
    rec_tdp = record.get("tdp_w")
    if row.tdp_w is not None and isinstance(rec_tdp, int) and not isinstance(rec_tdp, bool):
        tolerance = max(2, round(0.15 * max(rec_tdp, row.tdp_w)))
        if abs(rec_tdp - row.tdp_w) <= tolerance:
            agreements.append("tdp_w")
        else:
            conflicts.append("tdp_w")
    rec_clock = record.get("base_clock_mhz")
    if (
        row.base_clock_mhz is not None
        and isinstance(rec_clock, int)
        and not isinstance(rec_clock, bool)
    ):
        tolerance = max(10, round(0.03 * max(rec_clock, row.base_clock_mhz)))
        if abs(rec_clock - row.base_clock_mhz) <= tolerance:
            agreements.append("base_clock_mhz")
        else:
            conflicts.append("base_clock_mhz")
    if row.interfaces and rec_iface:
        if row.interfaces & rec_iface:
            agreements.append("interface")
        else:
            conflicts.append("interface")
    elif row.section_interface and rec_iface:
        # The bus lives in the section heading (AGP table vs PCIe table), not
        # in a cell. Matching is an agreement; AGP vs PCIe is a conflict.
        if row.section_interface in rec_iface:
            agreements.append("section_interface")
        else:
            conflicts.append("section_interface")
    rec_year = _record_year(record)
    if row.year is not None and rec_year is not None:
        if abs(row.year - rec_year) <= 1:
            agreements.append("release_year")
        else:
            conflicts.append("release_year")
    return agreements, conflicts


def _spec_rank(agreements: list[str]) -> int:
    keys = set(agreements)
    if "memory_gb" in keys or "memory_bus_bit" in keys:
        return 3
    if keys & {"tdp_w", "base_clock_mhz", "interface"}:
        return 2
    if keys & {"release_year", "section_interface"}:
        return 1
    return 0


@dataclass
class _Scored:
    row: WikiRow
    agreements: list[str]
    conflicts: list[str]


def _reason_alive(liveness: str) -> bool:
    """``classify`` reasons below HTTP 400 are live. 4xx/5xx and transport errors are not."""
    if not liveness.startswith("http-"):
        return False
    code = liveness.removeprefix("http-")
    return code.isdigit() and int(code) < 400


def _unconfirmed_suffix(card: CardName, agreements: list[str]) -> str | None:
    """A stripped memory or bus token must be restated by Wikipedia before CONFIRM."""
    if card.memory_gb is not None and "memory_gb" not in agreements:
        return "memory-suffix-unconfirmed"
    if card.interface is not None and "interface" not in agreements:
        return "interface-suffix-unconfirmed"
    return None


def _row_identity(row: WikiRow) -> tuple[str, str]:
    """Full model (codename kept) plus section. RV370 and RV380 stay distinct."""
    return (normalize_heading(row.model), normalize_heading(row.section or ""))


def _confirm_ready(agreements: list[str]) -> bool:
    return (
        len(agreements) >= MIN_CONFIRM_AGREEMENTS and _spec_rank(agreements) >= MIN_CONFIRM_RANK
    )


def decide(
    record: dict[str, Any], rows: list[WikiRow], *, liveness: str = "http-200"
) -> GateResult:
    """CONFIRM only when the heading, form factor, bus, and two specs agree."""
    name = record.get("name") if isinstance(record.get("name"), str) else ""
    card = split_card_name(name)
    alive = _reason_alive(liveness)
    if not card.base:
        return GateResult(NOTFOUND, None, None, None, liveness, [], [], "no-name", False, "")
    hits = [row for row in rows if _heading_matches(card.base, comparable_title(row.model))]
    if not hits:
        return GateResult(
            NOTFOUND, None, None, None, liveness, [], [], "no-heading", False, card.base
        )
    if not alive:
        return GateResult(
            NOTFOUND,
            None,
            hits[0].url,
            hits[0].model,
            liveness,
            [],
            [],
            "not-live",
            False,
            card.base,
        )
    # Drop a laptop row (or a desktop row) before scoring. Specs can match
    # across those lines; the marker is enough to refuse the pair.
    kept = [row for row in hits if not form_factor_conflict(name, row_form_text(row))]
    if not kept:
        sample = hits[0]
        return GateResult(
            AMBIGUOUS,
            None,
            sample.url,
            sample.model,
            liveness,
            [],
            [],
            "form-factor-variant",
            not brand_prefix_equal(card.base, comparable_title(sample.model)),
            card.base,
        )
    scored = [_Scored(row, *compare_specs(record, row, card)) for row in kept]
    clean = [item for item in scored if item.agreements and not item.conflicts]
    if clean:
        exact = [
            item
            for item in clean
            if brand_prefix_equal(card.base, comparable_title(item.row.model))
        ]
        pool = exact or clean
        best = max(_spec_rank(item.agreements) for item in pool)
        top = [item for item in pool if _spec_rank(item.agreements) == best]
        # Same comparable name with two codenames or two sections is not one card.
        if len({_row_identity(item.row) for item in top}) > 1:
            return GateResult(
                AMBIGUOUS,
                None,
                top[0].row.url,
                top[0].row.model,
                liveness,
                top[0].agreements,
                [],
                "multiple-rows",
                not brand_prefix_equal(card.base, comparable_title(top[0].row.model)),
                card.base,
            )
        chosen = max(top, key=lambda item: len(item.agreements))
        blocked = _unconfirmed_suffix(card, chosen.agreements)
        if blocked:
            return GateResult(
                AMBIGUOUS,
                None,
                chosen.row.url,
                chosen.row.model,
                liveness,
                chosen.agreements,
                chosen.conflicts,
                blocked,
                not brand_prefix_equal(card.base, comparable_title(chosen.row.model)),
                card.base,
            )
        if not _confirm_ready(chosen.agreements):
            return GateResult(
                AMBIGUOUS,
                None,
                chosen.row.url,
                chosen.row.model,
                liveness,
                chosen.agreements,
                chosen.conflicts,
                "insufficient-specs",
                not brand_prefix_equal(card.base, comparable_title(chosen.row.model)),
                card.base,
            )
        suffix_only = not brand_prefix_equal(card.base, comparable_title(chosen.row.model))
        return GateResult(
            CONFIRM,
            chosen.row.url,
            chosen.row.url,
            chosen.row.model,
            liveness,
            chosen.agreements,
            chosen.conflicts,
            "spec-agree",
            suffix_only,
            card.base,
        )
    mixed = [item for item in scored if item.agreements and item.conflicts]
    if mixed:
        sample = mixed[0]
        return GateResult(
            AMBIGUOUS,
            None,
            sample.row.url,
            sample.row.model,
            liveness,
            sample.agreements,
            sample.conflicts,
            "mixed-specs",
            not brand_prefix_equal(card.base, sample.row.model),
            card.base,
        )
    conflicted = [item for item in scored if item.conflicts and not item.agreements]
    if conflicted:
        headings = {normalize_heading(item.row.model) for item in conflicted}
        sample = conflicted[0]
        # A single documented card whose specs disagree is a contradiction.
        # Several different headings that all miss is just ambiguity.
        if len(headings) == 1:
            wiki_conflicts = [
                name for name in sample.conflicts if not name.startswith("name_")
            ]
            if wiki_conflicts:
                return GateResult(
                    CONTRADICT,
                    None,
                    sample.row.url,
                    sample.row.model,
                    liveness,
                    [],
                    sample.conflicts,
                    "spec-conflict",
                    not brand_prefix_equal(card.base, sample.row.model),
                    card.base,
                )
        return GateResult(
            AMBIGUOUS,
            None,
            sample.row.url,
            sample.row.model,
            liveness,
            [],
            sample.conflicts,
            "spec-conflict-ambiguous",
            not brand_prefix_equal(card.base, sample.row.model),
            card.base,
        )
    sample = scored[0]
    return GateResult(
        AMBIGUOUS,
        None,
        sample.row.url,
        sample.row.model,
        liveness,
        [],
        [],
        "no-comparable-spec",
        not brand_prefix_equal(card.base, sample.row.model),
        card.base,
    )


# --- cache / records -------------------------------------------------------------


def content_hash(record: dict[str, Any]) -> str:
    blob = json.dumps(record, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def append_cache(entry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(line + "\n")


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for entry in ledger.iter_entries(path):
        key = entry.get("path")
        if isinstance(key, str):
            out[key] = entry
    return out


def cache_entry(
    *, rel_path: str, record: dict[str, Any], result: GateResult, ts: str
) -> dict[str, Any]:
    return {
        "agreements": result.agreements,
        "base_name": result.base_name,
        "conflicts": result.conflicts,
        "decision": result.decision,
        "hash": content_hash(record),
        "inspected_url": result.inspected_url,
        "liveness": result.liveness,
        "name": record.get("name"),
        "path": rel_path,
        "proposed_url": result.proposed_url,
        "reason": result.reason,
        "slug": record.get("slug"),
        "suffix_only": result.suffix_only,
        "title": result.title,
        "ts": ts,
        "category": "gpu",
        "gate": GATE_VERSION,
    }


def is_kaggle_gpu(record: dict[str, Any]) -> bool:
    return record.get("source_urls") == [KAGGLE_GPU_URL]


def append_wikipedia_source(path: Path, url: str) -> str:
    """Append ``url`` to a kaggle-only ``source_urls`` list. Preserve formatting.

    Returns ``written``, ``present``, or ``skipped``.
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    record = json.loads(text)
    urls = record.get("source_urls")
    if not isinstance(urls, list) or not isinstance(url, str) or not url:
        return "skipped"
    if url in urls:
        return "present"
    if urls != [KAGGLE_GPU_URL]:
        return "skipped"
    normalized = text.replace("\r\n", "\n")
    quoted_old = json.dumps(KAGGLE_GPU_URL, ensure_ascii=False)
    quoted_new = json.dumps(url, ensure_ascii=False)
    old = f'"source_urls": [\n    {quoted_old}\n  ]'
    new = f'"source_urls": [\n    {quoted_old},\n    {quoted_new}\n  ]'
    if normalized.count(old) != 1:
        return "skipped"
    updated = normalized.replace(old, new, 1)
    if b"\r\n" in raw:
        updated = updated.replace("\n", "\r\n")
    path.write_bytes(updated.encode("utf-8"))
    return "written"


def gpu_scan_root(data_root: Path) -> tuple[Path, Path]:
    """Return ``(gpu_dir, repo_root)`` for a TechAPI checkout or a ``data/`` directory."""
    if (data_root / "data" / "gpu").is_dir():
        return data_root / "data" / "gpu", data_root
    if (data_root / "gpu").is_dir():
        parent = data_root.parent if data_root.name == "data" else data_root
        return data_root / "gpu", parent
    raise SystemExit(f"no data/gpu directory under {data_root}")


def iter_gpu_records(gpu_dir: Path, repo_root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(gpu_dir.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(record, dict):
            rel = path.relative_to(repo_root).as_posix()
            yield rel, record


def brand_of(record: dict[str, Any], rel_path: str) -> str:
    manufacturer = record.get("manufacturer")
    if isinstance(manufacturer, str) and manufacturer:
        return manufacturer
    parts = rel_path.split("/")
    if "gpu" in parts:
        index = parts.index("gpu")
        if index + 1 < len(parts):
            return parts[index + 1]
    return ""


def sample_diverse(
    rows: list[tuple[str, dict[str, Any]]], limit: int | None
) -> list[tuple[str, dict[str, Any]]]:
    """Round-robin across manufacturers so a limit is not one brand."""
    by_brand: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for rel, record in rows:
        by_brand.setdefault(brand_of(record, rel), []).append((rel, record))
    if limit is None:
        return [item for brand in sorted(by_brand) for item in by_brand[brand]]
    picked: list[tuple[str, dict[str, Any]]] = []
    cursors = {brand: 0 for brand in by_brand}
    while len(picked) < limit:
        progressed = False
        for brand in sorted(by_brand):
            index = cursors[brand]
            bucket = by_brand[brand]
            if index >= len(bucket):
                continue
            picked.append(bucket[index])
            cursors[brand] = index + 1
            progressed = True
            if len(picked) >= limit:
                break
        if not progressed:
            break
    return picked


# --- summary ---------------------------------------------------------------------


def _only(agreements: list[str], allowed: set[str]) -> bool:
    return bool(agreements) and set(agreements) <= allowed


def render_summary(result: RunResult, *, dry_run: bool, sleep_s: float) -> str:
    counts = result.counts()
    total = len(result.rows) or 1
    lines = [
        "# Wikipedia GPU backfill dry-run" if dry_run else "# Wikipedia GPU backfill",
        "",
        f"- records: **{len(result.rows)}** across **{len(result.brands)}** brands",
        f"- kaggle-only GPUs eligible: {result.eligible}",
        f"- index rows: {result.index_rows}",
        f"- http requests this process: {result.requests}",
        f"- sleep between requests: {max(sleep_s, MIN_SLEEP_S):.1f}s",
        f"- resumed from cache: {result.cached}",
        f"- dry-run: {dry_run}",
        f"- urls written: {result.written}",
        f"- writes skipped: {result.skipped_writes}",
        "",
        "## Pages",
        "",
    ]
    for page, count in sorted(result.index_pages.items()):
        lines.append(f"- `{page}`: {count} rows")
    lines.extend(
        [
            "",
            "## Decisions",
            "",
            "| decision | count | ratio |",
            "| --- | ---: | ---: |",
        ]
    )
    for name in DECISIONS:
        count = counts[name]
        lines.append(f"| {name.upper()} | {count} | {count / total:.1%} |")
    lines.append("")
    suffix_confirms = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM and row.get("suffix_only") and row.get("proposed_url")
    ]
    year_only = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM and _only(row.get("agreements") or [], {"release_year"})
    ]
    tdp_only = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM and _only(row.get("agreements") or [], {"tdp_w"})
    ]
    section_only = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM
        and _only(row.get("agreements") or [], {"release_year", "section_interface"})
        and "section_interface" in (row.get("agreements") or [])
    ]
    form_factor = [row for row in result.rows if row.get("reason") == "form-factor-variant"]
    weak_specs = [row for row in result.rows if row.get("reason") == "insufficient-specs"]
    lines.extend(
        [
            "## Mismatch signals",
            "",
            "- CONFIRM via a non-exact heading (extra SKU token, not a brand prefix): "
            f"{len(suffix_confirms)}",
            f"- CONFIRM whose only agreeing spec is launch year: {len(year_only)}",
            f"- CONFIRM whose only agreeing spec is TDP: {len(tdp_only)}",
            "- CONFIRM on a section bus hint plus year, with no table spec: "
            f"{len(section_only)}",
            f"- AMBIGUOUS desktop/mobile variant: {len(form_factor)}",
            f"- AMBIGUOUS fewer than two strong specs: {len(weak_specs)}",
            "",
        ]
    )
    for label, bucket in (
        ("Suffix-only CONFIRM", suffix_confirms),
        ("Year-only CONFIRM", year_only),
        ("TDP-only CONFIRM", tdp_only),
        ("Desktop/mobile AMBIGUOUS", form_factor),
        ("Weak-spec AMBIGUOUS", weak_specs),
    ):
        if not bucket:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for row in bucket[:25]:
            lines.append(
                f"- `{row['name']}` → `{row.get('title')}` "
                f"{row.get('proposed_url') or row.get('inspected_url')} "
                f"(agree {row.get('agreements')})"
            )
        lines.append("")
    lines.append("## Proposed source_urls (CONFIRM only)")
    lines.append("")
    confirms = [
        row for row in result.rows if row["decision"] == CONFIRM and row.get("proposed_url")
    ]
    if not confirms:
        lines.append("None.")
    for row in confirms:
        lines.append(
            f"- `{row['path']}` — {row.get('name')!r} vs {row.get('title')!r} "
            f"→ {row.get('proposed_url')} (agree {', '.join(row.get('agreements') or [])})"
        )
    lines.append("")
    lines.append("## Unresolved")
    lines.append("")
    unresolved = [row for row in result.rows if row["decision"] != CONFIRM]
    if not unresolved:
        lines.append("None.")
    for row in unresolved[:80]:
        lines.append(
            f"- `{str(row.get('decision', '')).upper()}` `{row['name']}` "
            f"({row.get('reason')}; inspected {row.get('inspected_url') or '—'})"
        )
    if len(unresolved) > 80:
        lines.append(f"- … {len(unresolved) - 80} more")
    lines.append("")
    if result.stopped:
        lines.append(f"Stopped: {result.stopped}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


# --- run -------------------------------------------------------------------------


class PoliteWiki:
    """One Wikipedia request at a time, with a pause before every request after the first."""

    def __init__(
        self, sleep_s: float, timeout: float = 120.0, cache_dir: Path | None = None
    ) -> None:
        self.sleep_s = max(sleep_s, MIN_SLEEP_S)
        self.timeout = timeout
        self.requests = 0
        self.cache_dir = cache_dir

    def _pause(self) -> None:
        if self.requests:
            time.sleep(self.sleep_s)
        self.requests += 1

    def fetch(self, page: str) -> tuple[int | None, str, str]:
        cached = self._read_cache(page)
        if cached is not None:
            return cached
        self._pause()
        url = WIKI_REST_HTML.format(title=quote(page, safe=""))
        try:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT, "Api-User-Agent": USER_AGENT},
                timeout=self.timeout,
                follow_redirects=True,
            ) as client:
                response = client.get(url)
        except httpx.HTTPError:
            return None, _article_url("", page), ""
        final = _article_url(str(response.url), page)
        body = response.text if response.status_code < 400 else ""
        if body:
            self._write_cache(page, body)
        return response.status_code, final, body

    def _cache_path(self, page: str) -> Path | None:
        if self.cache_dir is None:
            return None
        safe = re.sub(r"[^\w.-]+", "_", page)
        return self.cache_dir / f"{safe}.html"

    def _read_cache(self, page: str) -> tuple[int, str, str] | None:
        path = self._cache_path(page)
        if path is None or not path.is_file() or path.stat().st_size < 200:
            return None
        final = f"https://en.wikipedia.org/wiki/{quote(page, safe='/:')}"
        return 200, final, path.read_text(encoding="utf-8")

    def _write_cache(self, page: str, body: str) -> None:
        path = self._cache_path(page)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def search(self, name: str) -> list[Candidate]:
        self._pause()
        return WikipediaFetcher(timeout=self.timeout, limit=5).search(name)


def _page_from_wiki_url(url: str) -> str:
    path = unquote(urlparse(url).path)
    marker = "/wiki/"
    if marker not in path:
        return ""
    return path.split(marker, 1)[1].split("#", 1)[0]


def _liveness_for(url: str | None, page_liveness: dict[str, str]) -> str:
    if not url:
        return "error"
    page = _page_from_wiki_url(url)
    return page_liveness.get(page, "http-200")


def backfill(
    *,
    data_root: Path,
    cache_path: Path,
    summary_path: Path,
    limit: int | None,
    sleep_s: float,
    dry_run: bool,
    apply: bool,
    max_fallback: int = 80,
    pages: list[tuple[str, str, str]] | None = None,
    fetch_page: FetchPage | None = None,
    search_fn: SearchFn | None = None,
    records: list[tuple[str, dict[str, Any]]] | None = None,
) -> RunResult:
    """Match a diverse sample. ``apply`` writes CONFIRM urls; dry-run writes nothing."""
    if apply and dry_run:
        raise SystemExit("refusing --apply together with --dry-run")
    writing = bool(apply) and not dry_run
    client = (
        PoliteWiki(sleep_s, cache_dir=cache_path.parent / "pages") if fetch_page is None else None
    )
    fetch = fetch_page or (client.fetch if client else None)
    search = search_fn if search_fn is not None else (client.search if client else None)
    if fetch is None or search is None:
        raise SystemExit("missing wikipedia client")

    page_rows: list[WikiRow] = []
    page_counts: dict[str, int] = {}
    page_liveness: dict[str, str] = {}
    parsed_pages: set[str] = set()
    for _manufacturer, page, _fallback in pages if pages is not None else crossref_pages():
        print(f"fetch {page}", flush=True)
        status, final, html = fetch(page)
        print(f"fetched {page} status={status} bytes={len(html)}", flush=True)
        final_page = _page_from_wiki_url(final) or page
        alive, reason = classify(_article_url(final, page), status, final or None)
        page_liveness[page] = reason
        page_liveness[final_page] = reason
        parsed_pages.add(page)
        parsed_pages.add(final_page)
        if not alive:
            page_counts[page] = 0
            continue
        extracted = rows_from_html(html, final_page, final)
        page_counts[page] = len(extracted)
        page_rows.extend(extracted)

    repo_root: Path | None = None
    if records is None:
        gpu_dir, repo_root = gpu_scan_root(data_root)
        loaded = [
            (rel, rec)
            for rel, rec in iter_gpu_records(gpu_dir, repo_root)
            if is_kaggle_gpu(rec)
        ]
    else:
        loaded = [(rel, rec) for rel, rec in records if is_kaggle_gpu(rec)]
        if writing:
            _gpu_dir, repo_root = gpu_scan_root(data_root)
    chosen = sample_diverse(loaded, limit)
    cache = load_cache(cache_path)
    result = RunResult(
        eligible=len(loaded),
        index_rows=len(page_rows),
        index_pages=page_counts,
    )
    fetcher = WikipediaListFetcher(page_rows)
    fallback_fetches = 0
    attempted_fallback: set[str] = set()

    def consider_fallback(base: str) -> None:
        nonlocal fallback_fetches
        if not base or base in attempted_fallback or fallback_fetches >= max_fallback:
            return
        attempted_fallback.add(base)
        candidates = search(base)
        exact = [cand for cand in candidates if _heading_matches(base, cand.title)]
        if len(exact) != 1:
            if len(exact) > 1:
                fetcher.rows.extend(
                    WikiRow(model=cand.title, url=cand.url, page=_page_from_wiki_url(cand.url))
                    for cand in exact
                )
            return
        page = _page_from_wiki_url(exact[0].url)
        if not page or page in parsed_pages:
            return
        if fallback_fetches >= max_fallback:
            return
        fallback_fetches += 1
        status, final, html = fetch(page)
        final_page = _page_from_wiki_url(final) or page
        _alive, reason = classify(exact[0].url, status, final or None)
        page_liveness[page] = reason
        page_liveness[final_page] = reason
        parsed_pages.add(page)
        parsed_pages.add(final_page)
        if not html:
            return
        extracted = rows_from_html(html, final_page, final)
        page_counts[page] = page_counts.get(page, 0) + len(extracted)
        fetcher.rows.extend(extracted)
        result.index_rows = len(fetcher.rows)

    def maybe_write(rel: str, decision: object, url: object) -> None:
        if not writing or repo_root is None or decision != CONFIRM or not isinstance(url, str):
            return
        status = append_wikipedia_source(repo_root / rel, url)
        if status == "written":
            result.written += 1
            print(f"wrote {rel}", flush=True)
        elif status == "skipped":
            result.skipped_writes += 1
            print(f"skip-write {rel}", flush=True)

    for rel, record in chosen:
        result.brands.add(brand_of(record, rel))
        digest = content_hash(record)
        cached = cache.get(rel)
        if (
            cached
            and cached.get("hash") == digest
            and cached.get("decision") in DECISIONS
            and cached.get("gate") == GATE_VERSION
        ):
            result.rows.append({**cached, "cached": True})
            result.cached += 1
            maybe_write(rel, cached.get("decision"), cached.get("proposed_url"))
            continue
        name = record.get("name") if isinstance(record.get("name"), str) else ""
        card = split_card_name(name)
        hits = fetcher.rows_for(name)
        if not hits:
            consider_fallback(card.base)
            hits = fetcher.rows_for(name)
        live = _liveness_for(hits[0].url if hits else None, page_liveness)
        outcome = decide(record, hits, liveness=live if hits else "http-200")
        append_cache(
            cache_entry(rel_path=rel, record=record, result=outcome, ts=_now_iso()),
            cache_path,
        )
        result.rows.append(
            {
                "agreements": outcome.agreements,
                "base_name": outcome.base_name,
                "conflicts": outcome.conflicts,
                "decision": outcome.decision,
                "inspected_url": outcome.inspected_url,
                "name": record.get("name"),
                "path": rel,
                "proposed_url": outcome.proposed_url,
                "reason": outcome.reason,
                "suffix_only": outcome.suffix_only,
                "title": outcome.title,
                "cached": False,
            }
        )
        print(
            f"{outcome.decision.upper()} {record.get('name')} "
            f"{outcome.proposed_url or outcome.reason} "
            f"agree={outcome.agreements} conflict={outcome.conflicts}",
            flush=True,
        )
        maybe_write(rel, outcome.decision, outcome.proposed_url)
    result.requests = client.requests if client is not None else 0
    result.index_pages = page_counts
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        render_summary(result, dry_run=dry_run, sleep_s=sleep_s),
        encoding="utf-8",
    )
    return result


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _default_state_dir() -> Path:
    return Path(".wikipedia-gpu-backfill")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.verify.wikipedia_gpu_backfill")
    parser.add_argument(
        "--data-root",
        type=Path,
        required=True,
        help="TechAPI checkout or its data/ directory. Read-only unless --apply.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Max records this run.")
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.5,
        help="Seconds between Wikipedia requests (raised to 1.0 if lower).",
    )
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument(
        "--max-fallback",
        type=int,
        default=80,
        help="Extra article fetches for names missing from the list pages.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report decisions and do not write TechAPI files (the default).",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Append the Wikipedia URL onto source_urls for CONFIRM rows only.",
    )
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("refusing --apply together with --dry-run")
    dry_run = not args.apply
    state = _default_state_dir()
    summary = args.summary or (state / "summary.md")
    cache = args.cache or (state / "wikipedia_gpu_backfill_cache.jsonl")
    result = backfill(
        data_root=args.data_root,
        cache_path=cache,
        summary_path=summary,
        limit=args.limit,
        sleep_s=args.sleep,
        dry_run=dry_run,
        apply=args.apply,
        max_fallback=args.max_fallback,
    )
    counts = result.counts()
    print(
        " ".join(f"{name.upper()}={counts[name]}" for name in DECISIONS)
        + f" records={len(result.rows)} brands={len(result.brands)} "
        + f"requests={result.requests} written={result.written} dry_run={dry_run}"
    )
    if result.stopped:
        print(result.stopped, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
