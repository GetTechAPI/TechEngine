"""Backfill Wikipedia URLs onto SoC records that have no Wikipedia provenance.

TechAPI ``data/soc`` rows often cite only a dump (Kaggle, PhoneDB, and similar).
Those URLs do not identify the chip, so this tool cross-references English
Wikipedia list articles — the Tier-1 source SPEC.md allows — and appends that
article URL for rows the gate confirms.

The gate follows :mod:`app.verify.wikipedia_gpu_backfill`:

* the heading must match exactly after manufacturer, ``Bionic``, and a trailing
  ``5G`` word are folded; ``+`` / Plus, Pro, Max, Ultra, Lite, ``4G``, and
  ``for Galaxy`` stay in the identity
* a confirm needs two agreeing specs and rank at least 2, so a launch year
  alone cannot confirm, and neither can a process node or a GPU alone
* a wearable, automotive, modem, or PC/compute marker on only one side drops
  that row (the desktop/mobile rule, for SoC lines)
* two same-named rows that still disagree stay ambiguous
* a part number is used only when it points at one row
* liveness is :func:`app.verify.http_check.classify`
* the resume cache is append-only JSONL

Launch years in this dataset are often a January-1 bucket several years off the
Wikipedia date. A year outside ±1 is not an agreement and not a veto: a
conflicting process or GPU still blocks, and the year still cannot confirm by
itself.

``--dry-run`` (the default) never writes a TechAPI file. ``--apply`` appends
the Wikipedia URL for CONFIRM rows only. GSMArena, PhoneDB, and
DeviceSpecifications are never requested.

::

    python -m app.verify.wikipedia_soc_backfill \\
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
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

import httpx
from bs4 import BeautifulSoup, Tag

from app.ingest.sources.wikitable import _table_to_grid
from app.verify import ledger
from app.verify.crossref import AMBIGUOUS, CONFIRM, CONTRADICT, NOTFOUND
from app.verify.http_check import classify

USER_AGENT = (
    "TechEngine-verify/0.1 (https://github.com/GetTechAPI/TechEngine; soc wikipedia crossref)"
)
WIKI_REST_HTML = "https://en.wikipedia.org/api/rest_v1/page/html/{title}"
DECISIONS = (CONFIRM, AMBIGUOUS, NOTFOUND, CONTRADICT)
MIN_SLEEP_S = 1.0
GATE_VERSION = 2
MIN_CONFIRM_RANK = 2
MIN_CONFIRM_AGREEMENTS = 2
# Longest first so ``texasinstruments`` is removed before ``ti``.
_MAKERS = (
    "texasinstruments",
    "hisilicon",
    "spreadtrum",
    "qualcomm",
    "mediatek",
    "samsung",
    "nvidia",
    "unisoc",
    "google",
    "huawei",
    "marvell",
    "broadcom",
    "intel",
    "apple",
    "arm",
    "ti",
)
_PART_FIND = re.compile(
    r"\b(?:SM\d{3,5}(?:-[A-Z0-9]+)?|MSM\d{3,5}[A-Z]*|APQ\d{3,5}[A-Z]*|"
    r"QSC\d{3,5}[A-Z]*|QSD\d{3,5}[A-Z]*|MT\d{4}[A-Z0-9]*|S5E\d{3,5}|S5P\d{3,5}|"
    r"UMS\d{3,5}|UIS\d{3,5}[A-Z]*|SC\d{4}[A-Z]*|APL\s*[0-9A-Z]{3,}|"
    r"HI\d{3,5}[A-Z0-9]*|T\d{4}[A-Z0-9]*)\b",
    re.IGNORECASE,
)
_PART_WHOLE = re.compile(
    r"^(?:SM\d{3,5}(?:-[A-Z0-9]+)?|MSM\d{3,5}[A-Z]*|APQ\d{3,5}[A-Z]*|"
    r"QSC\d{3,5}[A-Z]*|QSD\d{3,5}[A-Z]*|MT\d{4}[A-Z0-9]*|S5E\d{3,5}|S5P\d{3,5}|"
    r"UMS\d{3,5}|UIS\d{3,5}[A-Z]*|SC\d{4}[A-Z]*|APL\s*[0-9A-Z]{3,}|"
    r"HI\d{3,5}[A-Z0-9]*|T\d{4}[A-Z0-9]*)$",
    re.IGNORECASE,
)
_FOOTNOTE_RE = re.compile(r"\[[^\]]*\]")
_PAREN_SKU_RE = re.compile(
    r"\b(4g|5g|plus|pro|max|ultra|lite|galaxy|se|fe)\b",
    re.IGNORECASE,
)
_NM_RE = re.compile(r"(\d+(?:\.\d+)?)\s*nm\b", re.IGNORECASE)
_NODE_RE = re.compile(r"\bN(\d)(?:[A-Z]+)?\b")
_YEAR_RE = re.compile(r"\b(19\d{2}|20\d{2})\b")
_GPU_ADRENO = re.compile(r"\badreno\s*(\d{2,4})\s*([a-z]{0,2})\b", re.IGNORECASE)
_GPU_MALI = re.compile(
    r"\b(immortalis|mali)[\s-]*([gt]-?\d{2,4}|\d{3,4})(?!\d)",
    re.IGNORECASE,
)
_GPU_PVR = re.compile(
    r"\b(?:powervr\s*)?(sgx|ge|gm|gx)\s*-?\s*(\d{3,4})(?!\d)",
    re.IGNORECASE,
)
_GPU_XCLIPSE = re.compile(r"\bx(?:clipse|lipse)\s*-?\s*(\d{3,4})(?!\d)", re.IGNORECASE)
_GPU_MALEOON = re.compile(r"\bmaleoon\s*-?\s*(\d{3,4})(?!\d)", re.IGNORECASE)
_GPU_VIDEOCORE = re.compile(r"\bvideocore\s*(iv|vii|vi|v|4|5|6|7)\b", re.IGNORECASE)
_GPU_CORES = re.compile(r"(?:mp|mc)\s*-?\s*(\d{1,3})(?!\d)", re.IGNORECASE)
_MULTIPLIER_RE = re.compile(r"(\d+)\s*[×x]\s*", re.IGNORECASE)
_PLUS_GROUP_RE = re.compile(r"\b(\d{1,2}(?:\s*\+\s*\d{1,2})+)\b")
_NAMED_CORES = (
    ("octa", 8),
    ("hexa", 6),
    ("quad", 4),
    ("triple", 3),
    ("dual", 2),
    ("single", 1),
)
_FORM_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("wearable", re.compile(r"\b(?:wearables?|smartwatches?|watches?)\b", re.IGNORECASE)),
    ("automotive", re.compile(r"\b(?:automotive|cockpit)\b|\bauto\b", re.IGNORECASE)),
    ("modem", re.compile(r"\bmodems?\b", re.IGNORECASE)),
    (
        "pc",
        re.compile(
            r"\b(?:laptops?|notebooks?|chromebooks?|compute)\b|\b8cx\b|\b7c\b|\b8c\b"
            r"|\bx\s+elite\b|\bx\s+plus\b",
            re.IGNORECASE,
        ),
    ),
)
_HEADER_START_RE = re.compile(r"\b(model|soc|name)\b", re.IGNORECASE)
_HEADER_SUB_RE = re.compile(
    r"\b(fab|isa|cores|freq|frequency|node|μarch|uarch|microarch)\b",
    re.IGNORECASE,
)
_DATA_ROW_RE = re.compile(
    r"\b(ghz|adreno|mali|snapdragon|exynos|dimensity|helio|kirin|immortalis|powervr)\b",
    re.IGNORECASE,
)

# (manufacturer, MediaWiki title, label). List articles only.
PAGES: tuple[tuple[str, str, str], ...] = (
    ("qualcomm", "List_of_Qualcomm_Snapdragon_systems_on_chips", "Snapdragon"),
    ("mediatek", "List_of_MediaTek_systems_on_chips", "MediaTek"),
    ("samsung", "Exynos", "Exynos"),
    ("apple", "Apple_silicon", "Apple silicon"),
    ("hisilicon", "HiSilicon", "HiSilicon"),
    ("unisoc", "List_of_UNISOC_systems_on_chips", "UNISOC"),
    ("nvidia", "Tegra", "Tegra"),
    ("google", "Google_Tensor", "Google Tensor"),
    ("texas-instruments", "OMAP", "OMAP"),
)

FetchPage = Callable[[str], tuple[int | None, str, str]]
SearchFn = Callable[[str], list[Any]]


@dataclass(frozen=True)
class GpuSpec:
    model: str | None
    cores: int | None


@dataclass(frozen=True)
class WikiRow:
    model: str
    url: str
    page: str
    section: str | None = None
    process_nm: frozenset[float] = frozenset()
    gpu_model: str | None = None
    gpu_cores: int | None = None
    cpu_cores: int | None = None
    year: int | None = None
    marketing_keys: frozenset[str] = frozenset()
    part_keys: frozenset[str] = frozenset()


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


def _clean(text: str) -> str:
    text = _FOOTNOTE_RE.sub("", text.replace("\xa0", " "))
    return re.sub(r"\s+", " ", text).strip(" -")


def soc_identity(text: str, *, fold_radio: bool = False) -> str:
    """Alphanumeric identity. Manufacturer, Bionic, and an optional ``5G`` word fold.

    ``+`` becomes ``plus``. Galaxy, Pro, Max, Ultra, Lite, and ``4G`` stay.
    """
    raw = text.replace("+", " plus ").replace("\xa0", " ")
    raw = _FOOTNOTE_RE.sub("", raw)

    def _keep_sku_paren(match: re.Match[str]) -> str:
        kept = _PAREN_SKU_RE.findall(match.group(1))
        return " " + " ".join(kept) + " " if kept else " "

    raw = re.sub(r"\(([^)]*)\)", _keep_sku_paren, raw)
    if fold_radio:
        raw = re.sub(r"\b5g\b", " ", raw, flags=re.IGNORECASE)
    raw = re.sub(r"\b(bionic|fusion|mobile platform)\b", " ", raw, flags=re.IGNORECASE)
    identity = re.sub(r"[^a-z0-9]+", "", raw.lower())
    changed = True
    while changed and identity:
        changed = False
        for maker in _MAKERS:
            if identity.startswith(maker) and len(identity) > len(maker):
                identity = identity[len(maker) :]
                changed = True
                break
    return identity


def _usable(identity: str) -> bool:
    if not 2 <= len(identity) <= 80:
        return False
    return bool(re.search(r"[a-z]", identity) and re.search(r"\d", identity))


def part_codes(text: str) -> list[str]:
    return [match.group(0) for match in _PART_FIND.finditer(text)]


def _is_part_token(text: str) -> bool:
    return bool(_PART_WHOLE.match(_clean(text).replace(" ", "")))


def _expand_labels(label: str) -> list[str]:
    cleaned = _clean(label)
    if not cleaned:
        return []
    pieces = [cleaned]
    for part in re.split(r"\s*/\s*|\s+\bor\b\s+", cleaned):
        part = _clean(part)
        if part and part not in pieces:
            pieces.append(part)
    tokens = [token.strip("(),") for token in cleaned.split()]
    if len(tokens) > 1 and all(_is_part_token(token) for token in tokens):
        for token in tokens:
            if token not in pieces:
                pieces.append(token)
    return pieces


def _label_keys(label: str) -> tuple[set[str], set[str]]:
    marketing: set[str] = set()
    parts: set[str] = set()
    without_paren = _clean(re.sub(r"\([^)]*\)", " ", label))
    extras = part_codes(label)
    candidates = _expand_labels(without_paren) + extras
    for piece in candidates:
        if _is_part_token(piece):
            ident = soc_identity(piece)
            if _usable(ident):
                parts.add(ident)
            continue
        ident = soc_identity(piece)
        # Wiki keys stay exact, including a 5G word. Folding 5G is record-side
        # only, so "Kirin 990" cannot land on "Kirin 990 5G".
        if _usable(ident):
            marketing.add(ident)
        for code in part_codes(piece):
            part_id = soc_identity(code)
            if _usable(part_id):
                parts.add(part_id)
    return marketing, parts


def _without_part_codes(text: str) -> str:
    return _PART_FIND.sub(" ", text)


def marketing_keys(name: str) -> set[str]:
    """Identity of the marketing name. Part numbers (SM8550-AB, S5E9925) are removed."""
    stripped = _without_part_codes(name)
    keys: set[str] = set()
    for builder in (soc_identity(stripped), soc_identity(stripped, fold_radio=True)):
        if _usable(builder):
            keys.add(builder)
    return keys


def record_part_keys(name: str) -> set[str]:
    return {soc_identity(code) for code in part_codes(name) if _usable(soc_identity(code))}


def process_nms(text: str) -> frozenset[float]:
    found: list[float] = []
    for match in _NM_RE.finditer(text):
        value = float(match.group(1))
        if 1 <= value <= 200 and value not in found:
            found.append(value)
    if found:
        return frozenset(found[:4])
    nodes: list[float] = []
    for match in _NODE_RE.finditer(text):
        value = float(match.group(1))
        if value not in nodes:
            nodes.append(value)
    return frozenset(nodes[:4])


def _year_of(text: str) -> int | None:
    years = [int(match) for match in _YEAR_RE.findall(text)]
    if not years or max(years) - min(years) > 1:
        return None
    return years[0]


def parse_gpu(text: str) -> GpuSpec:
    """Specific GPU model. ``Adreno`` or ``GeForce`` without a model number is ignored."""
    if not text:
        return GpuSpec(None, None)
    model: str | None = None
    adreno = _GPU_ADRENO.search(text)
    mali = _GPU_MALI.search(text)
    pvr = _GPU_PVR.search(text)
    xclipse = _GPU_XCLIPSE.search(text)
    maleoon = _GPU_MALEOON.search(text)
    videocore = _GPU_VIDEOCORE.search(text)
    if adreno:
        model = "adreno" + adreno.group(1) + adreno.group(2).lower()
    elif mali:
        model = mali.group(1).lower() + re.sub(r"[^a-z0-9]", "", mali.group(2).lower())
    elif pvr:
        model = pvr.group(1).lower() + pvr.group(2)
    elif xclipse:
        model = "xclipse" + xclipse.group(1)
    elif maleoon:
        model = "maleoon" + maleoon.group(1)
    elif videocore:
        roman = {"4": "iv", "5": "v", "6": "vi", "7": "vii"}.get(
            videocore.group(1).lower(), videocore.group(1).lower()
        )
        model = "videocore" + roman
    cores: int | None = None
    core_match = _GPU_CORES.search(text)
    if core_match:
        count = int(core_match.group(1))
        if 1 <= count <= 128:
            cores = count
    return GpuSpec(model, cores)


def cpu_total(text: str) -> int | None:
    """Total CPU cores. Frequency fragments such as ``A77 + 1.8`` are ignored."""
    if not text:
        return None
    bare = text.strip()
    if re.fullmatch(r"\d{1,2}", bare):
        count = int(bare)
        return count if 1 <= count <= 16 else None
    multipliers = [int(match) for match in _MULTIPLIER_RE.findall(text)]
    if multipliers and all(1 <= count <= 12 for count in multipliers):
        total = sum(multipliers)
        if 1 <= total <= 16:
            return total
        return None
    groups = []
    for group in _PLUS_GROUP_RE.findall(text):
        nums = [int(piece) for piece in re.findall(r"\d+", group)]
        if nums and all(num <= 12 for num in nums):
            groups.append(sum(nums))
    if len(set(groups)) == 1 and 1 <= groups[0] <= 16:
        return groups[0]
    if groups:
        return None
    for label, count in _NAMED_CORES:
        if re.search(rf"\b{label}[\s-]*cores?\b", text, re.IGNORECASE):
            return count
    word = re.search(r"\b(\d{1,2})\s+cores?\b", text, re.IGNORECASE)
    if word:
        count = int(word.group(1))
        if 1 <= count <= 16:
            return count
    return None


def form_factor_marks(name: str) -> frozenset[str]:
    """Wearable, automotive, modem, or PC markers. Phone SoCs carry none of these."""
    text = _clean(unquote(name)).replace("_", " ").replace("-", " ")
    text = re.sub(r"\([^)]*\)", " ", text)
    return frozenset(label for label, pattern in _FORM_RULES if pattern.search(text))


def form_factor_conflict(left: str, right: str) -> bool:
    return form_factor_marks(left) != form_factor_marks(right)


def row_form_text(row: WikiRow) -> str:
    return " ".join(part for part in (row.model, row.section, row.url) if part)


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


def _nearest_section_label(table: Tag) -> str | None:
    for prev in table.find_all_previous(["h2", "h3", "h4"]):
        if not isinstance(prev, Tag):
            continue
        text = _clean(prev.get_text(" ", strip=True))
        if text and "edit" not in text.lower():
            return text.split("[")[0].strip() or None
    return None


def _header_block(grid: list[list[str]]) -> tuple[int, int] | None:
    for start, row in enumerate(grid[:4]):
        blob = " ".join(row)
        if not _HEADER_START_RE.search(blob):
            continue
        depth = 1
        if start + 1 < len(grid):
            nxt = " ".join(grid[start + 1])
            if _HEADER_SUB_RE.search(nxt) and not _DATA_ROW_RE.search(nxt):
                depth = 2
        return start, depth
    return None


def _field_for(label: str) -> str | None:
    if "product name" in label or (
        re.search(r"\bname\b", label) is not None and "model" not in label and "part" not in label
    ):
        return "product"
    if "part number" in label or "part no" in label:
        return "part"
    # ``SoC / Fab`` is the process column. Check it before the bare ``soc`` model word.
    if any(token in label for token in ("fab", "process", "semiconductor")) or re.search(
        r"\bnode\b", label
    ):
        return "process"
    if "model" in label or re.search(r"\bsoc\b", label):
        return "model"
    if "gpu" in label and not any(token in label for token in ("freq", "gflops", "performance")):
        return "gpu"
    if "core" in label and "gpu" not in label and "freq" not in label and "isa" not in label:
        return "cpu_cores"
    if "cpu" in label and "isa" not in label and "cache" not in label:
        return "cpu"
    if any(token in label for token in ("released", "release", "sampling", "announced")):
        return "year"
    if re.search(r"\bavailability\b", label) and "sampling" not in label:
        return "year"
    return None


def _column_fields(grid: list[list[str]], start: int, depth: int) -> dict[int, str]:
    width = max((len(row) for row in grid[start : start + depth]), default=0)
    fields: dict[int, str] = {}
    for col in range(width):
        parts: list[str] = []
        for offset in range(depth):
            row = grid[start + offset]
            if col < len(row) and row[col]:
                parts.append(row[col])
        field_name = _field_for(" ".join(parts).lower())
        if field_name and col not in fields:
            fields[col] = field_name
    return fields


_CPU_ONLY_RE = re.compile(
    r"^(?:arm\s*11|arm\s*9|cortex[-\s]?[am]\d+|scorpion|krait)$",
    re.IGNORECASE,
)


def _display_label(labels: list[str]) -> str:
    """Prefer the chip name over a CPU-architecture cell in the same row."""
    cleaned = [_clean(label) for label in labels]
    for label in cleaned:
        if not _CPU_ONLY_RE.match(label):
            return label
    return cleaned[0]


def _plausible_label(text: str) -> bool:
    cleaned = _clean(text)
    if not 2 <= len(cleaned) <= 80:
        return False
    lowered = cleaned.lower()
    if lowered in {"model", "model number", "soc", "name", "product name", "part number"}:
        return False
    if "list of" in lowered or "features of" in lowered:
        return False
    return bool(re.search(r"[A-Za-z]", cleaned) and re.search(r"\d", cleaned))


def _row_from_mapped(
    *,
    cells: dict[str, str],
    page: str,
    page_url: str,
    section: str | None,
) -> WikiRow | None:
    labels = [cells[key] for key in ("product", "model", "part") if cells.get(key)]
    labels = [label for label in labels if _plausible_label(label)]
    if not labels:
        return None
    marketing: set[str] = set()
    parts: set[str] = set()
    for label in labels:
        label_marketing, label_parts = _label_keys(label)
        marketing |= label_marketing
        parts |= label_parts
    if not marketing and not parts:
        return None
    gpu_text = cells.get("gpu", "")
    gpu = parse_gpu(gpu_text)
    process_text = cells.get("process", "")
    processes = process_nms(process_text) if process_text else frozenset()
    cores = cpu_total(cells["cpu_cores"]) if cells.get("cpu_cores") else None
    if cores is None and cells.get("cpu"):
        cores = cpu_total(cells["cpu"])
    year = _year_of(cells["year"]) if cells.get("year") else None
    title = _display_label(labels)
    return WikiRow(
        model=title,
        url=_section_url(page_url, section),
        page=page,
        section=section,
        process_nm=processes,
        gpu_model=gpu.model,
        gpu_cores=gpu.cores,
        cpu_cores=cores,
        year=year,
        marketing_keys=frozenset(marketing),
        part_keys=frozenset(parts),
    )


def index_rows(rows: Iterable[WikiRow]) -> list[WikiRow]:
    """Drop part-number keys that hit more than one row (MT6983Z on 9000 and 9000+)."""
    listed = list(rows)
    counts: dict[str, int] = {}
    for row in listed:
        for key in row.part_keys:
            counts[key] = counts.get(key, 0) + 1
    prepared: list[WikiRow] = []
    for row in listed:
        unique = frozenset(key for key in row.part_keys if counts.get(key) == 1)
        prepared.append(replace(row, part_keys=unique))
    return prepared


def rows_from_html(html: str, page: str, page_url: str | None = None) -> list[WikiRow]:
    """One WikiRow per SoC table row. Transposed comparison tables yield nothing."""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    url = page_url or f"https://en.wikipedia.org/wiki/{quote(page, safe='/:')}"
    parsed: list[WikiRow] = []
    for table in soup.select("table.wikitable"):
        if not isinstance(table, Tag):
            continue
        grid = _table_to_grid(table)
        header = _header_block(grid)
        if header is None:
            continue
        start, depth = header
        fields = _column_fields(grid, start, depth)
        if "model" not in fields.values() and "product" not in fields.values():
            continue
        section = _nearest_section_label(table)
        for raw in grid[start + depth :]:
            cells: dict[str, str] = {}
            for col, field_name in fields.items():
                if col < len(raw) and raw[col] and field_name not in cells:
                    cells[field_name] = raw[col]
            row = _row_from_mapped(cells=cells, page=page, page_url=url, section=section)
            if row is not None:
                parsed.append(row)
    return index_rows(parsed)


def _record_process(record: dict[str, Any]) -> float | None:
    value = record.get("process_nm")
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


def _record_cpu_cores(record: dict[str, Any]) -> int | None:
    config = record.get("cpu_config")
    if not isinstance(config, dict):
        return None
    count = config.get("core_count")
    if isinstance(count, int) and not isinstance(count, bool) and 1 <= count <= 16:
        return count
    performance = config.get("performance")
    efficiency = config.get("efficiency")
    if (
        isinstance(performance, int)
        and not isinstance(performance, bool)
        and isinstance(efficiency, int)
        and not isinstance(efficiency, bool)
        and performance >= 0
        and efficiency >= 0
        and 1 <= performance + efficiency <= 16
    ):
        return performance + efficiency
    architecture = config.get("architecture")
    if isinstance(architecture, str):
        return cpu_total(architecture)
    return None


def _record_gpu(record: dict[str, Any]) -> GpuSpec:
    name = record.get("gpu_name")
    parsed = parse_gpu(name if isinstance(name, str) else "")
    cores = parsed.cores
    raw_cores = record.get("gpu_cores")
    if (
        cores is None
        and isinstance(raw_cores, int)
        and not isinstance(raw_cores, bool)
        and 1 <= raw_cores <= 128
    ):
        cores = raw_cores
    return GpuSpec(parsed.model, cores)


def compare_specs(record: dict[str, Any], row: WikiRow) -> tuple[list[str], list[str]]:
    """Independent spec agreements. A launch year outside ±1 is neither."""
    agreements: list[str] = []
    conflicts: list[str] = []
    rec_process = _record_process(record)
    if row.process_nm and rec_process is not None:
        if any(abs(rec_process - item) < 0.11 for item in row.process_nm):
            agreements.append("process_nm")
        else:
            conflicts.append("process_nm")
    rec_gpu = _record_gpu(record)
    if row.gpu_model and rec_gpu.model:
        if row.gpu_model == rec_gpu.model:
            agreements.append("gpu_model")
        else:
            conflicts.append("gpu_model")
    if row.gpu_cores is not None and rec_gpu.cores is not None:
        if row.gpu_cores == rec_gpu.cores:
            agreements.append("gpu_cores")
        else:
            conflicts.append("gpu_cores")
    rec_cores = _record_cpu_cores(record)
    if row.cpu_cores is not None and rec_cores is not None:
        if row.cpu_cores == rec_cores:
            agreements.append("cpu_cores")
        else:
            conflicts.append("cpu_cores")
    rec_year = _record_year(record)
    if row.year is not None and rec_year is not None and abs(row.year - rec_year) <= 1:
        agreements.append("release_year")
    return agreements, conflicts


def _spec_rank(agreements: list[str]) -> int:
    keys = set(agreements)
    if "gpu_model" in keys:
        return 3
    if "process_nm" in keys:
        return 2
    if keys & {"release_year", "cpu_cores", "gpu_cores"}:
        return 1
    return 0


def _confirm_ready(agreements: list[str]) -> bool:
    return len(agreements) >= MIN_CONFIRM_AGREEMENTS and _spec_rank(agreements) >= MIN_CONFIRM_RANK


@dataclass
class _Scored:
    row: WikiRow
    agreements: list[str]
    conflicts: list[str]


def _row_identity(row: WikiRow) -> tuple[str, str]:
    return (row.model, row.section or "")


def _reason_alive(liveness: str) -> bool:
    if not liveness.startswith("http-"):
        return False
    code = liveness.removeprefix("http-")
    return code.isdigit() and int(code) < 400


def matching_rows(name: str, rows: list[WikiRow]) -> list[WikiRow]:
    """Exact marketing identity. A part number never pulls in a different chip.

    A name that still has a marketing identity (Dimensity 7200-Ultra) does not
    fall back to a part number sitting on another row (Dimensity 7350). A unique
    part number only blocks a confirm when it points at a second row, or matches
    a record whose whole name is the part number (MSM7225).
    """
    marketing = marketing_keys(name)
    marketing_hits = [row for row in rows if marketing and marketing & row.marketing_keys]
    if marketing and not marketing_hits:
        return []
    parts = record_part_keys(name)
    part_hits = [row for row in rows if parts and parts & row.part_keys]

    def part_ok(row: WikiRow) -> bool:
        if not parts or not row.part_keys:
            return True
        return bool(parts & row.part_keys)

    if marketing_hits:
        # Hi3830 must not confirm against a Hi3630 row that only shares the marketing name.
        compatible = [row for row in marketing_hits if part_ok(row)]
        if not compatible:
            return part_hits
        extra = [row for row in part_hits if id(row) not in {id(hit) for hit in compatible}]
        return compatible + extra
    return part_hits


def same_chip(record_name: str, row: WikiRow) -> bool:
    """True when the row was reached by an exact marketing name or a unique part number."""
    if marketing_keys(record_name) & row.marketing_keys:
        return True
    return bool(record_part_keys(record_name) & row.part_keys)


def decide(
    record: dict[str, Any], rows: list[WikiRow], *, liveness: str = "http-200"
) -> GateResult:
    """CONFIRM only for an exact chip whose process or GPU agrees with a second spec."""
    raw_name = record.get("name")
    name = raw_name if isinstance(raw_name, str) else ""
    base = soc_identity(name, fold_radio=True)
    alive = _reason_alive(liveness)
    if not _usable(base):
        return GateResult(NOTFOUND, None, None, None, liveness, [], [], "no-name", False, "")
    hits = matching_rows(name, rows)
    if not hits:
        return GateResult(NOTFOUND, None, None, None, liveness, [], [], "no-heading", False, base)
    if not alive:
        return GateResult(
            NOTFOUND, None, hits[0].url, hits[0].model, liveness, [], [], "not-live", False, base
        )
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
            not same_chip(name, sample),
            base,
        )
    scored = [_Scored(row, *compare_specs(record, row)) for row in kept]
    clean = [item for item in scored if item.agreements and not item.conflicts]
    if clean:
        best = max(_spec_rank(item.agreements) for item in clean)
        top = [item for item in clean if _spec_rank(item.agreements) == best]
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
                not same_chip(name, top[0].row),
                base,
            )
        chosen = max(top, key=lambda item: len(item.agreements))
        loose = not same_chip(name, chosen.row)
        if loose or not _confirm_ready(chosen.agreements):
            return GateResult(
                AMBIGUOUS,
                None,
                chosen.row.url,
                chosen.row.model,
                liveness,
                chosen.agreements,
                chosen.conflicts,
                "loose-heading" if loose else "insufficient-specs",
                loose,
                base,
            )
        return GateResult(
            CONFIRM,
            chosen.row.url,
            chosen.row.url,
            chosen.row.model,
            liveness,
            chosen.agreements,
            [],
            "spec-agree",
            False,
            base,
        )
    mixed = [item for item in scored if item.agreements and item.conflicts]
    if mixed:
        mixed_row = mixed[0]
        return GateResult(
            AMBIGUOUS,
            None,
            mixed_row.row.url,
            mixed_row.row.model,
            liveness,
            mixed_row.agreements,
            mixed_row.conflicts,
            "mixed-specs",
            not same_chip(name, mixed_row.row),
            base,
        )
    conflicted = [item for item in scored if item.conflicts and not item.agreements]
    if conflicted:
        titles = {item.row.model for item in conflicted}
        conflict_row = conflicted[0]
        if len(titles) == 1:
            return GateResult(
                CONTRADICT,
                None,
                conflict_row.row.url,
                conflict_row.row.model,
                liveness,
                [],
                conflict_row.conflicts,
                "spec-conflict",
                not same_chip(name, conflict_row.row),
                base,
            )
        return GateResult(
            AMBIGUOUS,
            None,
            conflict_row.row.url,
            conflict_row.row.model,
            liveness,
            [],
            conflict_row.conflicts,
            "spec-conflict-ambiguous",
            not same_chip(name, conflict_row.row),
            base,
        )
    missed = scored[0]
    return GateResult(
        AMBIGUOUS,
        None,
        missed.row.url,
        missed.row.model,
        liveness,
        [],
        [],
        "no-comparable-spec",
        not same_chip(name, missed.row),
        base,
    )


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
        "category": "soc",
        "gate": GATE_VERSION,
    }


def needs_wikipedia_url(record: dict[str, Any]) -> bool:
    """True when ``source_urls`` has no English Wikipedia link yet."""
    urls = record.get("source_urls")
    if not isinstance(urls, list) or not urls:
        return False
    if not all(isinstance(item, str) for item in urls):
        return False
    return not any("wikipedia.org" in item for item in urls)


def append_wikipedia_source(path: Path, url: str) -> str:
    """Append ``url`` to ``source_urls``. Preserve indent and newlines.

    Returns ``written``, ``present``, or ``skipped``.
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8-sig")
    record = json.loads(text)
    urls = record.get("source_urls")
    if not isinstance(urls, list) or not all(isinstance(item, str) for item in urls):
        return "skipped"
    if not url or not isinstance(url, str):
        return "skipped"
    if url in urls:
        return "present"
    if any("wikipedia.org" in item for item in urls):
        return "skipped"
    if len(re.findall(r'"source_urls"\s*:', text)) != 1:
        return "skipped"
    match = re.search(r'"source_urls"\s*:\s*\[(.*?)\]', text, re.DOTALL)
    if match is None:
        return "skipped"
    body = match.group(1)
    if any(json.dumps(item, ensure_ascii=False) not in body for item in urls):
        return "skipped"
    key_indent_match = re.search(r"\n([ \t]*)\"source_urls\"", text)
    key_indent = key_indent_match.group(1) if key_indent_match else ""
    entry_indent_match = re.search(r"\n([ \t]+)", body)
    entry_indent = entry_indent_match.group(1) if entry_indent_match else "    "
    newline = "\r\n" if "\r\n" in text else "\n"
    combined = [*urls, url]
    lines = []
    for index, item in enumerate(combined):
        comma = "," if index < len(combined) - 1 else ""
        lines.append(f"{newline}{entry_indent}{json.dumps(item, ensure_ascii=False)}{comma}")
    new_block = '"source_urls": [' + "".join(lines) + f"{newline}{key_indent}]"
    updated = text[: match.start()] + new_block + text[match.end() :]
    parsed = json.loads(updated)
    if parsed.get("source_urls") != combined:
        return "skipped"
    path.write_bytes(updated.encode("utf-8"))
    return "written"


def soc_scan_root(data_root: Path) -> tuple[Path, Path]:
    """Return ``(soc_dir, repo_root)`` for a TechAPI checkout or a ``data/`` directory."""
    if (data_root / "data" / "soc").is_dir():
        return data_root / "data" / "soc", data_root
    if (data_root / "soc").is_dir():
        parent = data_root.parent if data_root.name == "data" else data_root
        return data_root / "soc", parent
    raise SystemExit(f"no data/soc directory under {data_root}")


def iter_soc_records(soc_dir: Path, repo_root: Path) -> Iterator[tuple[str, dict[str, Any]]]:
    for path in sorted(soc_dir.rglob("*.json")):
        if path.name.startswith("_"):
            continue
        record = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(record, dict):
            yield path.relative_to(repo_root).as_posix(), record


def brand_of(record: dict[str, Any], rel_path: str) -> str:
    manufacturer = record.get("manufacturer")
    if isinstance(manufacturer, str) and manufacturer:
        return manufacturer
    parts = rel_path.split("/")
    if "soc" in parts:
        index = parts.index("soc")
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


def _only(agreements: list[str], allowed: set[str]) -> bool:
    return bool(agreements) and set(agreements) <= allowed


def render_summary(result: RunResult, *, dry_run: bool, sleep_s: float) -> str:
    counts = result.counts()
    total = len(result.rows) or 1
    lines = [
        "# Wikipedia SoC backfill dry-run" if dry_run else "# Wikipedia SoC backfill",
        "",
        f"- records: **{len(result.rows)}** across **{len(result.brands)}** brands",
        f"- socs missing a Wikipedia URL: {result.eligible}",
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
    lines.extend(["", "## Decisions", "", "| decision | count | ratio |", "| --- | ---: | ---: |"])
    for name in DECISIONS:
        count = counts[name]
        lines.append(f"| {name.upper()} | {count} | {count / total:.1%} |")
    lines.append("")
    confirms = [
        row for row in result.rows if row["decision"] == CONFIRM and row.get("proposed_url")
    ]
    loose = [row for row in confirms if row.get("suffix_only") or not row.get("exact", True)]
    year_only = [row for row in confirms if _only(row.get("agreements") or [], {"release_year"})]
    process_only = [row for row in confirms if _only(row.get("agreements") or [], {"process_nm"})]
    gpu_only = [row for row in confirms if _only(row.get("agreements") or [], {"gpu_model"})]
    weak = [row for row in confirms if not _confirm_ready(row.get("agreements") or [])]
    form_factor = [row for row in result.rows if row.get("reason") == "form-factor-variant"]
    weak_ambiguous = [row for row in result.rows if row.get("reason") == "insufficient-specs"]
    lines.extend(
        [
            "## Mismatch signals",
            "",
            f"- CONFIRM via a non-exact heading: {len(loose)}",
            f"- CONFIRM whose only agreeing spec is launch year: {len(year_only)}",
            f"- CONFIRM whose only agreeing spec is process node: {len(process_only)}",
            f"- CONFIRM whose only agreeing spec is GPU: {len(gpu_only)}",
            f"- CONFIRM below the two-spec gate: {len(weak)}",
            f"- AMBIGUOUS wearable/auto/modem/PC variant: {len(form_factor)}",
            f"- AMBIGUOUS fewer than two strong specs: {len(weak_ambiguous)}",
            "",
        ]
    )
    lines.append("## Proposed source_urls (CONFIRM only)")
    lines.append("")
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


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def backfill(
    *,
    data_root: Path,
    cache_path: Path,
    summary_path: Path,
    limit: int | None,
    sleep_s: float,
    dry_run: bool,
    apply: bool,
    max_fallback: int = 0,
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
    if fetch is None:
        raise SystemExit("missing wikipedia client")

    page_rows: list[WikiRow] = []
    page_counts: dict[str, int] = {}
    page_liveness: dict[str, str] = {}
    for _manufacturer, page, _label in pages if pages is not None else list(PAGES):
        print(f"fetch {page}", flush=True)
        status, final, html = fetch(page)
        print(f"fetched {page} status={status} bytes={len(html)}", flush=True)
        final_page = _page_from_wiki_url(final) or page
        _alive, reason = classify(_article_url(final, page), status, final or None)
        page_liveness[page] = reason
        page_liveness[final_page] = reason
        if not _alive:
            page_counts[page] = 0
            continue
        extracted = rows_from_html(html, final_page, final)
        page_counts[page] = len(extracted)
        page_rows.extend(extracted)
    page_rows = index_rows(page_rows)

    repo_root: Path | None = None
    if records is None:
        soc_dir, repo_root = soc_scan_root(data_root)
        loaded = [
            (rel, rec)
            for rel, rec in iter_soc_records(soc_dir, repo_root)
            if needs_wikipedia_url(rec)
        ]
    else:
        loaded = [(rel, rec) for rel, rec in records if needs_wikipedia_url(rec)]
        if writing:
            _soc_dir, repo_root = soc_scan_root(data_root)
    chosen = sample_diverse(loaded, limit)
    cache = load_cache(cache_path)
    result = RunResult(eligible=len(loaded), index_rows=len(page_rows), index_pages=page_counts)
    # List articles are the only source. ``search_fn`` / ``max_fallback`` stay in the
    # signature so a later batch can opt into per-chip articles without a second tool.
    if max_fallback < 0 or (search_fn is not None and max_fallback < 0):
        raise SystemExit("max-fallback must be >= 0")

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
        raw_name = record.get("name")
        name = raw_name if isinstance(raw_name, str) else ""
        hits = matching_rows(name, page_rows)
        live = _liveness_for(hits[0].url if hits else None, page_liveness)
        outcome = decide(record, page_rows, liveness=live if hits else "http-200")
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


def _default_state_dir() -> Path:
    return Path(".wikipedia-soc-backfill")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="app.verify.wikipedia_soc_backfill")
    parser.add_argument("--data-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=None, help="Max records this run.")
    parser.add_argument("--sleep", type=float, default=1.5)
    parser.add_argument("--summary", type=Path, default=None)
    parser.add_argument("--cache", type=Path, default=None)
    parser.add_argument(
        "--max-fallback",
        type=int,
        default=0,
        help="Reserved. List pages are the only source; this stays 0.",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args(argv)
    if args.apply and args.dry_run:
        raise SystemExit("refusing --apply together with --dry-run")
    dry_run = not args.apply
    state = _default_state_dir()
    summary = args.summary or (state / "summary.md")
    cache = args.cache or (state / "wikipedia_soc_backfill_cache.jsonl")
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
