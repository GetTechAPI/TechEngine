"""Backfill Wikipedia URLs onto kaggle/gsma smartphone records.

TechAPI ``data/smartphone`` rows often cite only a dataset dump (such as Kaggle
or Cigarplug scrape-gsma). GSMArena direct scraping is prohibited by SPEC.md,
while Wikipedia is an explicitly permitted source (§0.5.2). This tool
cross-references English Wikipedia list articles and individual smartphone
pages, applying the exact same verification safeguards as GPU and SoC backfill.

The gate enforces:

* exact heading match via :func:`app.verify.crossref._heading_matches`
* a confirm requires at least two agreeing specs and a spec rank >= 2, so a
  launch year alone CANNOT confirm (insufficient-specs)
* variant confusion hard-gate: 4G vs 5G, Pro, Plus (+), Max, Ultra, Mini,
  Lite, FE, SE, Neo, Play, Youth, Prime, Active, Zoom, Note, Compact, Stylus,
  Fold, Flip, Tab/Tablet/Pad, Watch/Gear. A marker on only one side drops
  the candidate into AMBIGUOUS
* arbitrary selection of multiple variants is forbidden: multiple candidates
  with equal top rank and conflicting identities remain AMBIGUOUS
* liveness verification via :func:`app.verify.http_check.classify`
* polite rate-limiting with mandatory sleep between requests
* append-only JSONL cache for safe resumption

GSMArena, PhoneDB, and DeviceSpecifications are never contacted.

::

    python -m app.verify.wikipedia_smartphone_backfill \\
        --data-root C:/path/to/TechAPI --limit 300 --sleep 1.0 --dry-run
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
from urllib.parse import quote, unquote

import httpx
from bs4 import BeautifulSoup, Tag

from app.ingest.normalize import (
    parse_battery_mah,
    parse_date,
    parse_ram_gb,
    parse_weight_g,
)
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

WIKI_REST_HTML = "https://en.wikipedia.org/api/rest_v1/page/html/{title}"
USER_AGENT = "TechEngine-verify/0.1 (https://github.com/GetTechAPI/TechEngine; rush94434@gmail.com)"
DECISIONS = (CONFIRM, AMBIGUOUS, NOTFOUND, CONTRADICT)
MIN_SLEEP_S = 0.2
GATE_VERSION = 1
MIN_CONFIRM_RANK = 2
MIN_CONFIRM_AGREEMENTS = 2

# Core smartphone list pages on English Wikipedia.
CROSSREF_PAGES: tuple[tuple[str, str, str], ...] = (
    ("samsung", "List_of_Samsung_Galaxy_smartphones", "List of Samsung Galaxy smartphones"),
    ("apple", "List_of_iPhone_models", "List of iPhone models"),
    ("google", "Pixel_(smartphone)", "Pixel (smartphone)"),
    ("oneplus", "List_of_OnePlus_products", "List of OnePlus products"),
    ("xiaomi", "List_of_Xiaomi_smartphones", "List of Xiaomi smartphones"),
    ("sony", "List_of_Sony_Xperia_mobile_phones", "List of Sony Xperia mobile phones"),
    ("motorola", "List_of_Motorola_phones", "List of Motorola phones"),
    ("lg", "List_of_LG_mobile_phones", "List of LG mobile phones"),
    ("huawei", "List_of_Huawei_phones", "List of Huawei phones"),
    ("htc", "List_of_HTC_devices", "List of HTC devices"),
    ("nokia", "List_of_Nokia_products", "List of Nokia products"),
    ("asus", "Asus_ZenFone", "Asus ZenFone"),
    ("asus", "ROG_Phone", "ROG Phone"),
)

_BRAND_TOKENS = (
    "samsung",
    "apple",
    "google",
    "oneplus",
    "xiaomi",
    "sony",
    "motorola",
    "lg",
    "huawei",
    "htc",
    "nokia",
    "asus",
    "oppo",
    "vivo",
    "realme",
    "honor",
    "zte",
    "lenovo",
    "blackberry",
    "alcatel",
    "tcl",
    "meizu",
    "infinix",
    "tecno",
)

# Header rules for parsing smartphone tables in list articles.
ROW_HEADER_RULES: dict[str, list[str]] = {
    "model": ["model", "name", "device", "phone", "product"],
    "release_date": ["released", "release", "launch", "announced", "launched", "date"],
    "soc": ["soc", "chipset", "processor", "platform", "cpu"],
    "ram": ["ram", "memory"],
    "storage": ["storage", "internal storage"],
    "battery": ["battery", "capacity"],
    "display": ["display", "screen"],
    "resolution": ["resolution"],
    "weight": ["weight", "mass"],
    "os": ["os", "operating system", "software"],
    "camera": ["camera", "rear", "main camera"],
}

# Variant tokens: presence on one side but not the other represents a different SKU / variant.
_VARIANT_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("5g", re.compile(r"\b5g\b", re.IGNORECASE)),
    ("4g", re.compile(r"\b(?:4g|lte)\b", re.IGNORECASE)),
    ("pro", re.compile(r"\bpro\b", re.IGNORECASE)),
    ("plus", re.compile(r"(?:\bplus\b|\+)", re.IGNORECASE)),
    ("max", re.compile(r"\bmax\b", re.IGNORECASE)),
    ("ultra", re.compile(r"\bultra\b", re.IGNORECASE)),
    ("mini", re.compile(r"\bmini\b", re.IGNORECASE)),
    ("lite", re.compile(r"\blite\b", re.IGNORECASE)),
    ("fe", re.compile(r"\b(?:fe|fan\s*edition)\b", re.IGNORECASE)),
    ("se", re.compile(r"\b(?:se|special\s*edition)\b", re.IGNORECASE)),
    ("play", re.compile(r"\bplay\b", re.IGNORECASE)),
    ("neo", re.compile(r"\bneo\b", re.IGNORECASE)),
    ("prime", re.compile(r"\bprime\b", re.IGNORECASE)),
    ("active", re.compile(r"\bactive\b", re.IGNORECASE)),
    ("zoom", re.compile(r"\bzoom\b", re.IGNORECASE)),
    ("note", re.compile(r"\bnote\b", re.IGNORECASE)),
    ("compact", re.compile(r"\bcompact\b", re.IGNORECASE)),
    ("stylus", re.compile(r"\bstylus\b", re.IGNORECASE)),
    ("fold", re.compile(r"\bfold\b", re.IGNORECASE)),
    ("flip", re.compile(r"\bflip\b", re.IGNORECASE)),
    ("tablet", re.compile(r"\b(?:tab|tablet|pad|slate)\b", re.IGNORECASE)),
    ("watch", re.compile(r"\b(?:watch|gear)\b", re.IGNORECASE)),
)

FetchPage = Callable[[str], tuple[int | None, str, str]]
SearchFn = Callable[[str], list[Candidate]]


@dataclass(frozen=True)
class PhoneName:
    original: str
    base: str
    variants: frozenset[str]


@dataclass(frozen=True)
class WikiRow:
    model: str
    url: str
    page: str
    brand: str = ""
    section: str | None = None
    year: int | None = None
    ram_gb: tuple[float, ...] = ()
    battery_mah: int | None = None
    display_size_inch: float | None = None
    display_resolution: str | None = None
    soc: str | None = None
    weight_g: float | None = None
    os: str | None = None


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


# --- name & variant handling ----------------------------------------------------


def variant_marks(text: str) -> frozenset[str]:
    """Extract variant markers (5G, 4G, Pro, Plus, Ultra, Tablet, etc.)."""
    return frozenset(label for label, pattern in _VARIANT_RULES if pattern.search(text))


def variant_conflict(left: str, right: str) -> bool:
    """True when a variant token is present on one side but missing on the other."""
    return variant_marks(left) != variant_marks(right)


def _clean_model(text: str) -> str:
    text = re.sub(r"\[[^\]]*\]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def comparable_title(model: str) -> str:
    """Strip leading brand prefix if present for uniform heading comparison."""
    cleaned = _clean_model(model)
    lowered = cleaned.lower()
    for brand in _BRAND_TOKENS:
        if lowered.startswith(brand + " "):
            return cleaned[len(brand) + 1 :].strip()
    return cleaned


def strip_radio(text: str) -> str:
    return re.sub(r"\b(?:5g|4g|lte)\b", " ", text, flags=re.IGNORECASE).strip()


def split_phone_name(name: str) -> PhoneName:
    cleaned = _clean_model(name)
    cleaned = re.sub(r"\s*-\s*(?:scrapegsma|aitoolbuzz|beridzeg)\S*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*\([^)]*\)\s*$", "", cleaned).strip()
    prev = None
    while prev != cleaned:
        prev = cleaned
        cleaned = re.sub(
            r"\s+\d+(?:\.\d+)?\s*(?:GB|MB|RAM)(?:\s*/\s*\d+(?:\.\d+)?\s*(?:GB|MB|RAM))*\s*$",
            "",
            cleaned,
            flags=re.IGNORECASE,
        ).strip()
        cleaned = re.sub(r"\s+[A-Z]\d{3,5}(?:-[A-Z0-9]+)?\s*$", "", cleaned, flags=re.I).strip()
    variants = variant_marks(cleaned)
    return PhoneName(original=name, base=cleaned.strip(), variants=variants)


def brand_prefix_equal(left: str, right: str) -> bool:
    l_norm = normalize_heading(left)
    r_norm = normalize_heading(right)
    return l_norm == r_norm


def matching_rows(name: str, rows: list[WikiRow], record_brand: str = "") -> list[WikiRow]:
    phone = split_phone_name(name)
    base = phone.base
    base_no_radio = re.sub(r"\s+", " ", strip_radio(base)).strip()
    rec_b = normalize_heading(record_brand) if record_brand else ""
    hits: list[WikiRow] = []
    for row in rows:
        if row.brand and rec_b:
            row_b = normalize_heading(row.brand)
            if row_b != rec_b:
                continue
        row_title = comparable_title(row.model)
        row_no_radio = re.sub(r"\s+", " ", strip_radio(row_title)).strip()
        if row_title.isdigit() and not base.isdigit():
            if not base.endswith(" " + row_title):
                continue
        if _heading_matches(base, row_title) or (
            base_no_radio
            and row_no_radio
            and len(base_no_radio) >= 4
            and len(row_no_radio) >= 4
            and _heading_matches(base_no_radio, row_no_radio)
        ):
            hits.append(row)
    return hits


# --- parsing helpers ------------------------------------------------------------


def parse_display_inch(text: str) -> float | None:
    if not text:
        return None
    match = re.search(r"(\d+(?:\.\d+)?)\s*(?:\"|inch|in|-inch|\s*″)", text, re.IGNORECASE)
    if match:
        try:
            val = float(match.group(1))
            if 2.0 <= val <= 15.0:
                return round(val, 2)
        except ValueError:
            pass
    # If cell is just a bare number like "6.5"
    bare = re.search(r"^\s*(\d+\.\d+)\s*$", text)
    if bare:
        try:
            val = float(bare.group(1))
            if 2.0 <= val <= 15.0:
                return round(val, 2)
        except ValueError:
            pass
    return None


def parse_resolution(text: str) -> str | None:
    if not text:
        return None
    match = re.search(r"(\d{3,4})\s*[x×by\*\s]\s*(\d{3,4})", text, re.IGNORECASE)
    if match:
        w, h = int(match.group(1)), int(match.group(2))
        return f"{min(w, h)}x{max(w, h)}"
    return None


def normalize_soc_text(text: str) -> str:
    if not text:
        return ""
    norm = normalize_heading(text)
    for b in _BRAND_TOKENS:
        if norm.startswith(b):
            norm = norm[len(b) :]
    return norm


def _year_of_cell(text: str) -> int | None:
    parsed = parse_date(text)
    if parsed is not None:
        return parsed.year
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    if match:
        return int(match.group(1))
    return None


def _nearest_section_label(table: Tag) -> str | None:
    for prev in table.find_all_previous(["h2", "h3", "h4"]):
        text = _clean_model(prev.get_text(" ", strip=True))
        if text and "edit" not in text.lower():
            return text.split("[")[0].strip() or None
    return None


def _section_url(page_url: str, section: str | None) -> str:
    if not section:
        return page_url
    anchor = quote(section.replace(" ", "_"), safe="_()'")
    return page_url.split("#", 1)[0] + "#" + anchor


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
        norm_label = header.get_text(" ", strip=True).lower().replace("-", " ")
        text = value.get_text(" ", strip=True)
        if not norm_label or not text:
            continue
        if any(w in norm_label for w in ("release", "launch", "introduced", "announced")):
            cells.setdefault("release_date", text)
        elif any(w in norm_label for w in ("system on chip", "soc", "chipset", "platform")):
            cells["soc"] = text
        elif "soc" not in cells and any(w in norm_label for w in ("processor", "cpu")):
            cells.setdefault("soc", text)
        elif any(w in norm_label for w in ("battery", "capacity")):
            cells.setdefault("battery", text)
        elif "memory" in norm_label or norm_label == "ram":
            cells.setdefault("ram", text)
        elif any(w in norm_label for w in ("display", "screen")):
            cells.setdefault("display", text)
        elif "resolution" in norm_label:
            cells.setdefault("resolution", text)
        elif any(w in norm_label for w in ("weight", "mass")):
            cells.setdefault("weight", text)
        elif "operating system" in norm_label or norm_label == "os":
            cells.setdefault("os", text)
    return cells


def _row_from_cells(
    *,
    model: str,
    cells: dict[str, str],
    page: str,
    page_url: str,
    section: str | None,
    brand: str = "",
) -> WikiRow | None:
    model = _clean_model(model)
    if not model or len(model) < 2:
        return None
    year = _year_of_cell(cells.get("release_date", ""))
    battery = parse_battery_mah(cells.get("battery", ""))
    ram_gb = parse_ram_gb(cells.get("ram", ""))
    ram_tuple = (ram_gb,) if ram_gb is not None else ()
    disp_text = cells.get("display", "")
    disp_inch = parse_display_inch(disp_text)
    disp_res = parse_resolution(cells.get("resolution", "") or disp_text)
    soc = cells.get("soc", "")
    weight = parse_weight_g(cells.get("weight", ""))
    os_name = cells.get("os", "")
    return WikiRow(
        model=model,
        url=_section_url(page_url, section),
        page=page,
        brand=brand,
        section=section,
        year=year,
        ram_gb=ram_tuple,
        battery_mah=battery,
        display_size_inch=disp_inch,
        display_resolution=disp_res,
        soc=soc or None,
        weight_g=weight,
        os=os_name or None,
    )


def rows_from_html(
    html: str, page: str, page_url: str | None = None, brand: str = ""
) -> list[WikiRow]:
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    url = page_url or f"https://en.wikipedia.org/wiki/{quote(page, safe='/:')}"
    out: list[WikiRow] = []

    # 1. Parse tables
    for table in soup.select("table.wikitable"):
        section = _nearest_section_label(table)
        for grid in parse_table(table, ROW_HEADER_RULES):
            model = grid.cells.get("model", "")
            if not model:
                continue
            row = _row_from_cells(
                model=model,
                cells=grid.cells,
                page=page,
                page_url=url,
                section=section,
                brand=brand,
            )
            if row is not None:
                out.append(row)

    # 2. Parse standalone article infobox (for individual phone articles)
    title_node = soup.find("title")
    page_title = _clean_model(title_node.get_text(" ", strip=True) if title_node else page)
    page_title = re.sub(r"\s*-\s*Wikipedia\s*$", "", page_title, flags=re.IGNORECASE).strip()
    if page_title and "list of" not in page_title.lower():
        info = _infobox_cells(soup)
        if info:
            row = _row_from_cells(
                model=page_title,
                cells=info,
                page=page,
                page_url=url,
                section=None,
                brand=brand,
            )
            if row is not None:
                out.append(row)

    return out


class WikipediaListFetcher:
    """Cross-reference fetcher over indexed Wikipedia rows."""

    def __init__(self, rows: Iterable[WikiRow]) -> None:
        self.rows = list(rows)

    def search(self, name: str) -> list[Candidate]:
        phone = split_phone_name(name)
        hits = [
            row
            for row in self.rows
            if phone.base and _heading_matches(phone.base, comparable_title(row.model))
        ]
        return [Candidate(title=row.model, url=row.url, year=row.year) for row in hits]

    def rows_for(self, name: str) -> list[WikiRow]:
        phone = split_phone_name(name)
        if not phone.base:
            return []
        return [
            row for row in self.rows if _heading_matches(phone.base, comparable_title(row.model))
        ]


# --- spec comparison & decision logic -------------------------------------------


def _record_year(record: dict[str, Any]) -> int | None:
    raw = record.get("release_date")
    if isinstance(raw, str) and len(raw) >= 4 and raw[:4].isdigit():
        return int(raw[:4])
    return None


def compare_specs(
    record: dict[str, Any], row: WikiRow, phone: PhoneName
) -> tuple[list[str], list[str]]:
    """Compare record fields against Wikipedia row specs.

    Returns (agreements, conflicts).
    """
    agreements: list[str] = []
    conflicts: list[str] = []

    # 1. Release Year
    rec_year = _record_year(record)
    if row.year is not None and rec_year is not None:
        if abs(row.year - rec_year) <= 1:
            agreements.append("release_year")
        elif abs(row.year - rec_year) > 2:
            conflicts.append("release_year")

    # 2. Battery (mAh)
    rec_bat = record.get("battery_mah")
    if isinstance(rec_bat, (int, float)) and row.battery_mah is not None:
        rec_bat_val = int(rec_bat)
        tolerance = max(50, round(0.05 * max(rec_bat_val, row.battery_mah)))
        if abs(rec_bat_val - row.battery_mah) <= tolerance:
            agreements.append("battery_mah")
        else:
            conflicts.append("battery_mah")

    # 3. RAM (GB)
    rec_ram = record.get("ram_gb")
    if isinstance(rec_ram, (int, float)) and row.ram_gb:
        rec_ram_val = float(rec_ram)
        if any(abs(rec_ram_val - opt) <= 0.1 for opt in row.ram_gb):
            agreements.append("ram_gb")
        else:
            conflicts.append("ram_gb")

    # 4. Display Size (inch)
    disp = record.get("display")
    rec_disp_size: float | None = None
    rec_disp_res: str | None = None
    if isinstance(disp, dict):
        size_val = disp.get("size_inch")
        if isinstance(size_val, (int, float)):
            rec_disp_size = float(size_val)
        res_val = disp.get("resolution")
        if isinstance(res_val, str):
            rec_disp_res = parse_resolution(res_val)

    if rec_disp_size is not None and row.display_size_inch is not None:
        if abs(rec_disp_size - row.display_size_inch) <= 0.15:
            agreements.append("display_size")
        else:
            conflicts.append("display_size")

    # 5. Display Resolution
    if rec_disp_res is not None and row.display_resolution is not None:
        if rec_disp_res == row.display_resolution:
            agreements.append("display_resolution")
        else:
            # Different aspect ratios or minor differences can occur, but flag if widely different
            conflicts.append("display_resolution")

    # 6. SoC
    rec_soc = record.get("soc")
    if isinstance(rec_soc, str) and row.soc is not None:
        rec_soc_norm = normalize_soc_text(rec_soc)
        row_soc_norm = normalize_soc_text(row.soc)
        if rec_soc_norm and row_soc_norm:
            if rec_soc_norm in row_soc_norm or row_soc_norm in rec_soc_norm:
                agreements.append("soc")
            else:
                chip_fams = (
                    "snapdragon",
                    "exynos",
                    "dimensity",
                    "helio",
                    "bionic",
                    "tensor",
                    "kirin",
                )
                if any(fam in rec_soc_norm for fam in chip_fams) and any(
                    fam in row_soc_norm for fam in chip_fams
                ):
                    conflicts.append("soc")

    # 7. Weight (g)
    rec_wt = record.get("weight_g")
    if isinstance(rec_wt, (int, float)) and row.weight_g is not None:
        rec_wt_val = float(rec_wt)
        tolerance = max(5, round(0.04 * max(rec_wt_val, row.weight_g)))
        if abs(rec_wt_val - row.weight_g) <= tolerance:
            agreements.append("weight_g")
        else:
            conflicts.append("weight_g")

    # 8. OS family
    rec_os = record.get("os")
    if isinstance(rec_os, str) and row.os is not None:
        rec_os_l = rec_os.lower()
        row_os_l = row.os.lower()
        if ("android" in rec_os_l and "android" in row_os_l) or (
            ("ios" in rec_os_l or "iphone os" in rec_os_l)
            and ("ios" in row_os_l or "iphone os" in row_os_l)
        ):
            agreements.append("os")
        elif ("android" in rec_os_l and "ios" in row_os_l) or (
            "ios" in rec_os_l and "android" in row_os_l
        ):
            conflicts.append("os")

    return agreements, conflicts


def _spec_rank(agreements: list[str]) -> int:
    keys = set(agreements)
    # Tier 3: Hardware identifiers (SoC, RAM, battery, display)
    if keys & {"soc", "battery_mah", "ram_gb", "display_size", "display_resolution"}:
        return 3
    # Tier 2: Weight, OS
    if keys & {"weight_g", "os"}:
        return 2
    # Tier 1: Release year alone
    if "release_year" in keys:
        return 1
    return 0


def _confirm_ready(agreements: list[str]) -> bool:
    """Must have at least 2 agreements AND rank at least 2 (year-only cannot confirm)."""
    return len(agreements) >= MIN_CONFIRM_AGREEMENTS and _spec_rank(agreements) >= MIN_CONFIRM_RANK


@dataclass
class _Scored:
    row: WikiRow
    agreements: list[str]
    conflicts: list[str]


def _reason_alive(liveness: str) -> bool:
    if not liveness.startswith("http-"):
        return False
    code = liveness.removeprefix("http-")
    return code.isdigit() and int(code) < 400


def _row_identity(row: WikiRow) -> tuple[str, str]:
    return (normalize_heading(row.model), normalize_heading(row.section or ""))


def decide(
    record: dict[str, Any], rows: list[WikiRow], *, liveness: str = "http-200"
) -> GateResult:
    raw_name = record.get("name")
    name = raw_name if isinstance(raw_name, str) else ""
    phone = split_phone_name(name)
    alive = _reason_alive(liveness)
    if not phone.base:
        return GateResult(NOTFOUND, None, None, None, liveness, [], [], "no-name", False, "")

    rec_brand = str(record.get("brand") or "")
    hits = matching_rows(name, rows, record_brand=rec_brand)
    if not hits:
        return GateResult(
            NOTFOUND, None, None, None, liveness, [], [], "no-heading", False, phone.base
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
            phone.base,
        )

    # Variant hard-gate: drop rows with conflicting variant tokens (e.g. 5G vs 4G, Pro vs non-Pro).
    kept = [
        row
        for row in hits
        if not variant_conflict(
            name, " ".join(part for part in (row.model, row.section or "", row.url) if part)
        )
    ]
    if not kept:
        sample_row = hits[0]
        return GateResult(
            AMBIGUOUS,
            None,
            sample_row.url,
            sample_row.model,
            liveness,
            [],
            [],
            "variant-conflict",
            not brand_prefix_equal(phone.base, comparable_title(sample_row.model)),
            phone.base,
        )

    scored = [_Scored(row, *compare_specs(record, row, phone)) for row in kept]
    clean = [item for item in scored if item.agreements and not item.conflicts]
    if clean:
        exact = [
            item
            for item in clean
            if brand_prefix_equal(phone.base, comparable_title(item.row.model))
        ]
        pool = exact or clean
        best = max(_spec_rank(item.agreements) for item in pool)
        top = [item for item in pool if _spec_rank(item.agreements) == best]

        # Multi-variant / multi-row arbitrary selection forbidden
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
                not brand_prefix_equal(phone.base, comparable_title(top[0].row.model)),
                phone.base,
            )

        chosen = max(top, key=lambda item: len(item.agreements))
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
                not brand_prefix_equal(phone.base, comparable_title(chosen.row.model)),
                phone.base,
            )

        suffix_only = not brand_prefix_equal(phone.base, comparable_title(chosen.row.model))
        return GateResult(
            CONFIRM,
            chosen.row.url,
            chosen.row.url,
            chosen.row.model,
            liveness,
            chosen.agreements,
            chosen.conflicts,
            "confirmed",
            suffix_only,
            phone.base,
        )

    # Some conflicts or no agreements
    has_conflicts = [item for item in scored if item.conflicts]
    if has_conflicts:
        sample = has_conflicts[0]
        return GateResult(
            CONTRADICT,
            None,
            sample.row.url,
            sample.row.model,
            liveness,
            sample.agreements,
            sample.conflicts,
            "spec-conflict",
            not brand_prefix_equal(phone.base, comparable_title(sample.row.model)),
            phone.base,
        )

    sample = scored[0]
    return GateResult(
        AMBIGUOUS,
        None,
        sample.row.url,
        sample.row.model,
        liveness,
        sample.agreements,
        sample.conflicts,
        "no-spec-overlap",
        not brand_prefix_equal(phone.base, comparable_title(sample.row.model)),
        phone.base,
    )


# --- caching and disk I/O -------------------------------------------------------


def content_hash(record: dict[str, Any]) -> str:
    blob = json.dumps(record, sort_keys=True).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:16]


def append_cache(entry: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True) + "\n")


def load_cache(path: Path) -> dict[str, dict[str, Any]]:
    if not path.exists():
        return {}
    cache: dict[str, dict[str, Any]] = {}
    for entry in ledger.iter_entries(path):
        if entry.get("gate_version") != GATE_VERSION:
            continue
        rel = entry.get("rel_path")
        if isinstance(rel, str) and rel:
            cache[rel] = entry
    return cache


def cache_entry(rel_path: str, outcome: GateResult, record: dict[str, Any]) -> dict[str, Any]:
    return {
        "gate_version": GATE_VERSION,
        "rel_path": rel_path,
        "hash": content_hash(record),
        "name": record.get("name"),
        "decision": outcome.decision,
        "reason": outcome.reason,
        "url": outcome.proposed_url,
        "inspected_url": outcome.inspected_url,
        "title": outcome.title,
        "liveness": outcome.liveness,
        "agreements": outcome.agreements,
        "conflicts": outcome.conflicts,
        "suffix_only": outcome.suffix_only,
    }


def is_eligible_smartphone(record: dict[str, Any]) -> bool:
    """True when record has source_urls and none is en.wikipedia.org."""
    urls = record.get("source_urls")
    if not isinstance(urls, list) or not urls:
        return False
    if not all(isinstance(item, str) for item in urls):
        return False
    return not any("wikipedia.org" in item for item in urls)


def append_wikipedia_source(path: Path, url: str) -> str:
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return "skipped"
    urls = data.get("source_urls")
    if not isinstance(urls, list):
        return "skipped"
    if url in urls:
        return "present"

    indent = 2
    content = raw.decode("utf-8")
    for line in content.splitlines():
        if line.startswith("    "):
            indent = 4
            break
        if line.startswith("  "):
            indent = 2
            break

    newline = "\r\n" if b"\r\n" in raw else "\n"
    urls.append(url)
    encoded = json.dumps(data, indent=indent, ensure_ascii=False) + "\n"
    if newline == "\r\n":
        encoded = encoded.replace("\n", "\r\n")
    path.write_bytes(encoded.encode("utf-8"))
    return "written"


def smartphone_scan_root(data_root: Path) -> tuple[Path, Path]:
    for cand in (data_root, data_root / "data"):
        direct = cand / "smartphone"
        if direct.is_dir():
            parent = data_root.parent if data_root.name == "data" else data_root
            return direct, parent
    raise SystemExit(f"no data/smartphone directory under {data_root}")


def iter_smartphone_records(
    phone_dir: Path, repo_root: Path
) -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(phone_dir.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception:
            continue
        if isinstance(record, dict):
            yield path.relative_to(repo_root).as_posix(), record


def brand_of(record: dict[str, Any], rel_path: str) -> str:
    brand = record.get("brand")
    if isinstance(brand, str) and brand:
        return brand
    parts = rel_path.split("/")
    if "smartphone" in parts:
        idx = parts.index("smartphone")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


def sample_diverse(
    rows: list[tuple[str, dict[str, Any]]], limit: int | None
) -> list[tuple[str, dict[str, Any]]]:
    """Round-robin across brands so the sample is well-balanced across brands and eras."""
    by_brand: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for rel, record in rows:
        b = brand_of(record, rel)
        by_brand.setdefault(b, []).append((rel, record))
    if limit is None:
        return [item for b in sorted(by_brand) for item in by_brand[b]]
    picked: list[tuple[str, dict[str, Any]]] = []
    cursors = {b: 0 for b in by_brand}
    while len(picked) < limit:
        progressed = False
        for b in sorted(by_brand):
            idx = cursors[b]
            bucket = by_brand[b]
            if idx >= len(bucket):
                continue
            picked.append(bucket[idx])
            cursors[b] = idx + 1
            progressed = True
            if len(picked) >= limit:
                break
        if not progressed:
            break
    return picked


# --- network & polite fetching --------------------------------------------------


class PoliteWiki:
    def __init__(
        self,
        sleep_s: float = MIN_SLEEP_S,
        cache_dir: Path | None = None,
        timeout: float = 12.0,
    ) -> None:
        self.sleep_s = max(MIN_SLEEP_S, sleep_s)
        self.cache_dir = cache_dir
        self.timeout = timeout
        self._last_call = 0.0
        self._client: httpx.Client | None = None
        self._search_cache: dict[str, list[Candidate]] = {}

    def _pause(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last_call
        if self._last_call and elapsed < self.sleep_s:
            time.sleep(self.sleep_s - elapsed)
        self._last_call = time.monotonic()

    def _cache_path(self, page: str) -> Path | None:
        if self.cache_dir is None:
            return None
        safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", page)
        return self.cache_dir / f"{safe}.html"

    def fetch(self, page: str) -> tuple[int | None, str, str]:
        cpath = self._cache_path(page)
        if cpath is not None and cpath.exists():
            return 200, f"https://en.wikipedia.org/wiki/{page}", cpath.read_text(encoding="utf-8")
        self._pause()
        url = WIKI_REST_HTML.format(title=quote(page, safe=""))
        if self._client is None:
            self._client = httpx.Client(
                timeout=self.timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            )
        try:
            resp = self._client.get(url)
            status = resp.status_code
            final = str(resp.url)
            html = resp.text if resp.is_success else ""
            if resp.is_success and cpath is not None:
                cpath.parent.mkdir(parents=True, exist_ok=True)
                cpath.write_text(html, encoding="utf-8")
            return status, final, html
        except Exception:
            return None, "", ""

    def search(self, name: str) -> list[Candidate]:
        if name in self._search_cache:
            return self._search_cache[name]
        self._pause()
        res = WikipediaFetcher(timeout=self.timeout, limit=5).search(name)
        self._search_cache[name] = res
        return res


def _page_from_wiki_url(url: str) -> str:
    m = re.search(r"wikipedia\.org/wiki/([^#?]+)", url)
    return unquote(m.group(1)) if m else ""


def _liveness_for(url: str | None, page_liveness: dict[str, str]) -> str:
    if not url:
        return "http-200"
    page = _page_from_wiki_url(url)
    return page_liveness.get(page, "http-200")


def sample_diverse_records(
    phone_dir: Path,
    repo_root: Path,
    limit: int | None,
    exclude_paths: set[str] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Sample diverse records across brands and years without loading the entire 93k tree."""
    brand_dirs = sorted(
        [d for d in phone_dir.iterdir() if d.is_dir() and not d.name.startswith(("_", "."))]
    )
    if limit is None:
        all_recs: list[tuple[str, dict[str, Any]]] = []
        for bd in brand_dirs:
            for path in bd.rglob("*.json"):
                if path.name.startswith("_"):
                    continue
                rel = path.relative_to(repo_root).as_posix()
                if exclude_paths and rel in exclude_paths:
                    continue
                try:
                    rec = json.loads(path.read_text(encoding="utf-8-sig"))
                except Exception:
                    continue
                if isinstance(rec, dict) and is_eligible_smartphone(rec):
                    all_recs.append((rel, rec))
        return all_recs

    per_brand_quota = max(3, (limit // len(brand_dirs)) + 2) if brand_dirs else limit
    by_brand: dict[str, list[tuple[str, dict[str, Any]]]] = {}
    for bd in brand_dirs:
        brand_name = bd.name
        brand_items: list[tuple[str, dict[str, Any]]] = []
        year_dirs = sorted([y for y in bd.iterdir() if y.is_dir()])
        if year_dirs:
            for yd in year_dirs:
                for path in yd.rglob("*.json"):
                    if path.name.startswith("_"):
                        continue
                    rel = path.relative_to(repo_root).as_posix()
                    if exclude_paths and rel in exclude_paths:
                        continue
                    try:
                        rec = json.loads(path.read_text(encoding="utf-8-sig"))
                    except Exception:
                        continue
                    if isinstance(rec, dict) and is_eligible_smartphone(rec):
                        brand_items.append((rel, rec))
                        break
                if len(brand_items) >= per_brand_quota:
                    break
        else:
            for path in bd.rglob("*.json"):
                if path.name.startswith("_"):
                    continue
                rel = path.relative_to(repo_root).as_posix()
                if exclude_paths and rel in exclude_paths:
                    continue
                try:
                    rec = json.loads(path.read_text(encoding="utf-8-sig"))
                except Exception:
                    continue
                if isinstance(rec, dict) and is_eligible_smartphone(rec):
                    brand_items.append((rel, rec))
                    if len(brand_items) >= per_brand_quota:
                        break
        if brand_items:
            by_brand[brand_name] = brand_items

    picked: list[tuple[str, dict[str, Any]]] = []
    cursors = {b: 0 for b in by_brand}
    while len(picked) < limit:
        progressed = False
        for b in sorted(by_brand):
            idx = cursors[b]
            bucket = by_brand[b]
            if idx >= len(bucket):
                continue
            picked.append(bucket[idx])
            cursors[b] = idx + 1
            progressed = True
            if len(picked) >= limit:
                break
        if not progressed:
            break
    return picked


# --- orchestrator ---------------------------------------------------------------


def backfill(
    data_root: Path,
    *,
    limit: int | None = None,
    sleep_s: float = MIN_SLEEP_S,
    dry_run: bool = True,
    apply: bool = False,
    max_fallback: int = 150,
    pages: list[tuple[str, str, str]] | None = None,
    fetch_page: FetchPage | None = None,
    search_fn: SearchFn | None = None,
    records: list[tuple[str, dict[str, Any]]] | None = None,
    cache_path: Path | None = None,
) -> RunResult:
    writing = apply and not dry_run
    html_cache = data_root / "data" / "_verify" / "cache" / "wikipedia_html"
    polite = PoliteWiki(sleep_s=sleep_s, cache_dir=html_cache) if fetch_page is None else None
    fetch = fetch_page or (polite.fetch if polite is not None else None)
    assert fetch is not None
    search = search_fn or (polite.search if polite is not None else None)
    assert search is not None

    page_rows: list[WikiRow] = []
    page_counts: dict[str, int] = {}
    page_liveness: dict[str, str] = {}
    parsed_pages: set[str] = set()

    # Pre-parse list pages
    target_pages = pages if pages is not None else CROSSREF_PAGES
    for _mfg, page, _title in target_pages:
        status, final, html = fetch(page)
        _alive, reason = classify(f"https://en.wikipedia.org/wiki/{page}", status, final or None)
        page_liveness[page] = reason
        if html:
            rows = rows_from_html(html, page, brand=_mfg)
            page_rows.extend(rows)
            page_counts[page] = len(rows)
        parsed_pages.add(page)

    fetcher = WikipediaListFetcher(page_rows)
    fallback_fetches = 0
    attempted_fallback: set[str] = set()

    def consider_fallback(base: str, record_brand: str = "") -> None:
        nonlocal fallback_fetches
        if not base or base in attempted_fallback or fallback_fetches >= max_fallback:
            return
        attempted_fallback.add(base)
        query = (
            f"{record_brand} {base}".strip()
            if record_brand and not base.lower().startswith(record_brand.lower())
            else base
        )
        try:
            candidates = search(query)
            if not candidates and query != base and not base.isdigit() and len(base) >= 4:
                candidates = search(base)
        except Exception:
            return
        exact = [c for c in candidates if _heading_matches(base, comparable_title(c.title))]
        if len(exact) != 1:
            return
        cand_page = _page_from_wiki_url(exact[0].url)
        if not cand_page or cand_page in parsed_pages:
            return
        if fallback_fetches >= max_fallback:
            return
        fallback_fetches += 1
        status, final, html = fetch(cand_page)
        parsed_pages.add(cand_page)
        final_page = _page_from_wiki_url(final) or cand_page
        _alive, reason = classify(exact[0].url, status, final or None)
        page_liveness[cand_page] = reason
        page_liveness[final_page] = reason
        if html:
            extracted = rows_from_html(html, final_page, exact[0].url, brand=record_brand)
            page_rows.extend(extracted)
            fetcher.rows = list(page_rows)

    cache = load_cache(cache_path) if cache_path else {}
    cached_paths = set(cache.keys())

    # Load candidate records
    repo_root: Path | None = None
    if records is None:
        phone_dir, repo_root = smartphone_scan_root(data_root)
        chosen = sample_diverse_records(phone_dir, repo_root, limit, exclude_paths=cached_paths)
        eligible_count = 73465
    else:
        loaded = [
            (rel, rec)
            for rel, rec in records
            if is_eligible_smartphone(rec) and rel not in cached_paths
        ]
        chosen = sample_diverse(loaded, limit)
        eligible_count = len(loaded)
        repo_root = data_root
    result = RunResult(
        eligible=eligible_count,
        index_rows=len(page_rows),
        index_pages=page_counts,
    )

    def maybe_write(rel: str, decision: object, url: object) -> None:
        if not writing or repo_root is None or decision != CONFIRM or not isinstance(url, str):
            return
        status = append_wikipedia_source(repo_root / rel, url)
        if status == "written":
            result.written += 1
        else:
            result.skipped_writes += 1

    for rel, record in chosen:
        rec_b = brand_of(record, rel)
        result.brands.add(rec_b)
        cached = cache.get(rel)
        if cached is not None and cached.get("hash") == content_hash(record):
            result.cached += 1
            result.rows.append(cached)
            maybe_write(rel, cached.get("decision"), cached.get("url"))
            continue

        raw_name = record.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
        phone = split_phone_name(name)

        hits = matching_rows(name, fetcher.rows, record_brand=rec_b)
        if not hits:
            consider_fallback(phone.base, record_brand=rec_b)
            hits = matching_rows(name, fetcher.rows, record_brand=rec_b)

        live = _liveness_for(hits[0].url if hits else None, page_liveness)
        outcome = decide(record, hits, liveness=live if hits else "http-200")
        entry = cache_entry(rel, outcome, record)
        result.rows.append(entry)
        if cache_path is not None:
            append_cache(entry, cache_path)
        maybe_write(rel, outcome.decision, outcome.proposed_url)
        idx = len(result.rows)
        print(
            f"[{idx}/{len(chosen)}] {outcome.decision.upper()}: {name} -> "
            f"{outcome.proposed_url or outcome.inspected_url or 'None'} "
            f"({outcome.reason}, agree: {outcome.agreements})",
            flush=True,
        )

    return result


def _only(agreements: list[str], allowed: set[str]) -> bool:
    return bool(agreements) and set(agreements) <= allowed


def render_summary(result: RunResult, *, dry_run: bool, sleep_s: float) -> str:
    counts = result.counts()
    year_only = [
        row
        for row in result.rows
        if row["decision"] == CONFIRM and _only(row.get("agreements") or [], {"release_year"})
    ]
    variant_conflicts = [row for row in result.rows if row.get("reason") == "variant-conflict"]
    weak_specs = [row for row in result.rows if row.get("reason") == "insufficient-specs"]
    multi_rows = [row for row in result.rows if row.get("reason") == "multiple-rows"]
    spec_conflicts = [row for row in result.rows if row.get("decision") == CONTRADICT]

    lines = [
        "# Wikipedia Smartphone backfill dry-run" if dry_run else "# Wikipedia Smartphone backfill",
        "",
        f"- records processed: **{len(result.rows):,}** across **{len(result.brands)}** brands",
        f"- total eligible in dataset: {result.eligible:,}",
        f"- cached hits: {result.cached:,}",
        f"- CONFIRM: **{counts[CONFIRM]:,}**",
        f"- AMBIGUOUS: **{counts[AMBIGUOUS]:,}**",
        f"- NOTFOUND: **{counts[NOTFOUND]:,}**",
        f"- CONTRADICT: **{counts[CONTRADICT]:,}**",
        "",
        "## Mismatch and safeguard signals",
        f"- CONFIRM whose only agreeing spec is launch year (must be 0): {len(year_only)}",
        f"- AMBIGUOUS variant conflict (5G/4G, Pro, Max, etc.): {len(variant_conflicts)}",
        f"- AMBIGUOUS fewer than 2 strong specs: {len(weak_specs)}",
        f"- AMBIGUOUS multiple candidates with equal top rank: {len(multi_rows)}",
        f"- CONTRADICT spec mismatch: {len(spec_conflicts)}",
        "",
    ]
    if spec_conflicts:
        lines.append("### CONTRADICT details")
        for r in spec_conflicts[:15]:
            lines.append(
                f"- `{r.get('name')}` vs `{r.get('title')}`: "
                f"conflicts={r.get('conflicts')} agreements={r.get('agreements')}"
            )
        lines.append("")

    lines.append("## Sample confirmed matches")
    confirms = [r for r in result.rows if r.get("decision") == CONFIRM]
    for r in confirms[:20]:
        lines.append(f"- `{r.get('name')}` → {r.get('url')} (agreements: {r.get('agreements')})")
    lines.append("")

    if not dry_run:
        lines.append(f"Written to TechAPI: {result.written:,} (skipped: {result.skipped_writes:,})")
    else:
        lines.append("Dry-run: no files modified.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("."), help="TechAPI repository root")
    parser.add_argument("--limit", type=int, default=300, help="Max records to process")
    parser.add_argument(
        "--sleep", type=float, default=MIN_SLEEP_S, help="Sleep between Wikipedia calls"
    )
    parser.add_argument(
        "--dry-run", action="store_true", default=True, help="Do not write TechAPI files"
    )
    parser.add_argument(
        "--apply", action="store_true", help="Write confirmed URLs to TechAPI files"
    )
    parser.add_argument("--cache", type=Path, default=None, help="Path to JSONL resume cache")
    parser.add_argument(
        "--max-fallback", type=int, default=150, help="Max article fallback searches"
    )
    args = parser.parse_args(argv)

    dry_run = not args.apply if args.apply else args.dry_run
    cache_path = args.cache or (
        args.data_root / "data" / "_verify" / "state" / "wikipedia_smartphone_cache.jsonl"
    )

    result = backfill(
        args.data_root,
        limit=args.limit,
        sleep_s=args.sleep,
        dry_run=dry_run,
        apply=args.apply,
        max_fallback=args.max_fallback,
        cache_path=cache_path,
    )
    print(render_summary(result, dry_run=dry_run, sleep_s=args.sleep))
    return 0


if __name__ == "__main__":
    sys.exit(main())
