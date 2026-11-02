"""Wikipedia GPU list pages → ``IngestCandidate`` rows.

The ``List_of_<vendor>_graphics_processing_units`` pages use multi-row
headers (``Memory`` spanning ``Size (MiB) | Bus type | Bus width (bit)``) and
put units in the header, leaving bare numbers in the cells ("Core clock
(MHz)" → ``350``). Columns are therefore classified from the joined header
label and the header's unit is applied to unit-less cells.

Required GPU schema fields: ``architecture``, ``release_date``,
``memory_gb``, ``memory_type``, ``memory_bus_bit``, ``base_clock_mhz``,
``boost_clock_mhz``, ``tdp_w``, ``pcie_version``. Rows missing any of them
stay out of the PR unless ``--include-drafts``.

``architecture`` follows the dataset's convention (microarchitecture such as
``Kepler`` / ``TeraScale 2``), not the per-chip code name the tables list; it
comes from an explicit Architecture column or from the code name via
:func:`architecture_from_codename`. Rows whose code name maps to nothing are
left without an architecture (incomplete) rather than guessed.

A value is written only when its cell states it unambiguously. A cell that
lists several variants ("GDDR3 GDDR4 GDDR5", "AGP 8× PCIe ×16", "512 1024"),
a range ("100-260"), an estimate ("~75", "45?") or a year alone leaves the
field empty, so the row is skipped rather than given one variant's value.
Integrated GPUs are out of scope, and a laptop table row must name the laptop
part ("GeForce GTX 1070 (Notebook)" is ``geforce-gtx-1070-mobile``).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator
from datetime import date
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.coverage.normalize import slugify
from app.coverage.sources.wikipedia import fetch_wikipedia_html

from ..normalize import guess_gpu_segment, mib_to_gb, parse_date_period, release_date_of
from .base import IngestCandidate
from .wikitable import enclosing_headings, parse_table_header_block

# ``Nvidia_Quadro`` redirects to ``Quadro``, whose AGP/PCI/PCIe and NVS tables
# carry Quadro4-era boards the GeForce list does not. ATI Rage/Radeon legacy
# and FireGL/FirePro/Radeon Pro workstation sections are already on
# ``List_of_AMD_graphics_processing_units``. There is no
# ``List_of_AMD_workstation_graphics_processing_units`` article.
PAGES: list[tuple[str, str, str]] = [
    ("nvidia", "List_of_Nvidia_graphics_processing_units", "NVIDIA GeForce"),
    ("nvidia", "Quadro", "NVIDIA Quadro"),
    ("amd", "List_of_AMD_graphics_processing_units", "AMD Radeon"),
    ("intel", "List_of_Intel_graphics_processing_units", "Intel Graphics"),
]

_BRAND_DISPLAY: dict[str, str] = {"nvidia": "NVIDIA", "amd": "AMD", "ati": "ATI", "intel": "Intel"}

# The dataset keeps ATI-branded boards under ``ati``; AMD retired the ATI brand
# in August 2010 and the curated records switch to ``amd`` from October 2010.
_ATI_BRAND_END = date(2010, 10, 1)
# GPU Boost / PowerTune Boost arrived in 2012; before that one core clock is both.
_BOOST_ERA = date(2012, 1, 1)

MEMORY_TYPES = (
    "GDDR7", "GDDR6X", "GDDR6", "GDDR5X", "GDDR5", "GDDR4", "GDDR3", "GDDR2",
    "HBM3E", "HBM3", "HBM2E", "HBM2", "HBM",
    "LPDDR5X", "LPDDR5", "LPDDR4X", "LPDDR4",
    "DDR5", "DDR4", "DDR3", "DDR2", "DDR", "SDRAM", "SDR",
)
_MEMORY_TYPE_DISPLAY = {"HBM2E": "HBM2e", "HBM3E": "HBM3e"}

_NUMBER_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")
_RANGE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?:–|—|-|to)\s*(\d[\d,]*(?:\.\d+)?)")
_FOOTNOTE_RE = re.compile(r"\s*\[[^\]]{1,12}\]")
_TRAILING_PAREN_RE = re.compile(r"\s*\([^)]*\)\s*$")
_MULTI_GPU_RE = re.compile(r"\d\s*[×x]\s*\d")
_BUS_RE = re.compile(r"\b(PCIe|PCI-E|PCI Express|AGP|PCI|MXM(?:-[A-Z]+)?)\b[^/,;]*", re.IGNORECASE)
_BUS_NAME_RE = re.compile(r"\b(PCIe|AGP|PCI|MXM)\b", re.IGNORECASE)
# Integrated/chipset graphics are out of scope for the discrete-GPU table.
_OUT_OF_SCOPE_RE = re.compile(r"\bIGP\b|integrated|on-?die|\bAPU\b|nForce", re.IGNORECASE)
# Chipset IGPs: a memory cell naming only system memory ("Up to 256 from system
# memory", "System shared"), or an internal bus. HyperMemory and TurboCache
# boards state their onboard memory ("128 + shared", "256 onboard + up to 512 system").
_SYSTEM_MEMORY_RE = re.compile(r"\bsystem\b", re.IGNORECASE)
_ONBOARD_RE = re.compile(r"onboard|\+")
_CHIPSET_BUS_RE = re.compile(r"hyper\s*transport|\binternal\b", re.IGNORECASE)
# "(Notebook)" names the laptop part; it is not a remark to strip.
_LAPTOP_PAREN_RE = re.compile(r"\s*\((?:notebook|laptop|mobile)\)\s*$", re.IGNORECASE)
# A row in a laptop table names its laptop part: "Mobile", "Max-Q", "Go",
# "Mobility", an M model ("GTX 980M", "M1000M", "MX150").
_LAPTOP_MARKER_RE = re.compile(
    r"\b(?:mobile|mobility|laptop|notebook|max-q|go)\b|\b[a-z]?\d{2,5}m[a-z]*\b|\bm\d{3,4}|\bmx\s?\d",
    re.IGNORECASE,
)
_BRAND_WORD_RE = re.compile(r"\b(?:geforce|radeon|quadro|firepro|firegl|tesla)\b", re.IGNORECASE)
# "Clock Rate / Base (Boost) ( MHz )": "1291 (1392)" is base 1291, boost 1392.
_BASE_BOOST_RE = re.compile(r"\b(?:base|core)\b[^/]*\(\s*boost|\bbase boost\b")
_BASE_BOOST_CELL_RE = re.compile(r"\s*(\d[\d,]*)\s*\(\s*(\d[\d,]*)\s*\)\s*")
_DATE_IN_CELL_RES = (
    re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bQ[1-4]\s*'?\d{2,4}\b", re.IGNORECASE),
    re.compile(r"\b(?:[12]H|H[12])\s*\d{4}\b", re.IGNORECASE),
    re.compile(
        r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?,?\s+\d{4}\b",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:19|20)\d{2}\b"),
)

_KNOWN_ARCHITECTURES = (
    "Ada Lovelace", "Blackwell", "Hopper", "Ampere", "Turing", "Volta", "Pascal",
    "Maxwell", "Kepler", "Fermi", "Tesla", "Curie", "Rankine", "Kelvin", "Celsius",
    "TeraScale 3", "TeraScale 2", "TeraScale", "Ultra-Threaded SE",
)
_VERSIONED_ARCH_RE = re.compile(r"\b(RDNA|CDNA|GCN)\s*(\d)(?:\.(\d))?\b", re.IGNORECASE)

_NVIDIA_CODENAME_ARCH: tuple[tuple[str, str], ...] = (
    (r"NV1\d", "Celsius"),
    (r"NV2\d", "Kelvin"),
    (r"NV3\d", "Rankine"),
    (r"NV4\d|G7\d", "Curie"),
    (r"G8\d|G9\d|GT2\d\d", "Tesla"),
    (r"GF1\d\d", "Fermi"),
    (r"GK\d", "Kepler"),
    (r"GM\d", "Maxwell"),
    (r"GP\d", "Pascal"),
    (r"GV\d", "Volta"),
    (r"TU\d", "Turing"),
    (r"GA\d", "Ampere"),
    (r"AD\d", "Ada Lovelace"),
    (r"GH\d", "Hopper"),
    (r"GB\d", "Blackwell"),
)
_ATI_CODENAME_ARCH: tuple[tuple[str, str], ...] = (
    (r"R2\d\d|RV2\d\d", "R200"),
    (r"R3\d\d|RV3\d\d", "R300"),
    (r"R4\d\d|RV4\d\d", "R400"),
    (r"R5\d\d|RV5\d\d", "Ultra-Threaded SE"),
    (r"R6\d\d|RV6\d\d|RV7\d\d", "TeraScale"),
    (r"RV8\d\d|Cedar|Redwood|Juniper|Cypress|Hemlock|Caicos|Turks|Barts", "TeraScale 2"),
    (r"Cayman|Antilles", "TeraScale 3"),
)


def architecture_from_codename(codename: str, manufacturer: str) -> str | None:
    """Map a chip code name to the dataset's microarchitecture name.

    ``"NV34GL"`` → ``"Rankine"``; ``"2× G98-850"`` → ``"Tesla"``;
    ``"Redwood XT GL (RV830)"`` → ``"TeraScale 2"``. Returns ``None`` when the
    code name matches no known family — callers must not guess.
    """
    text = codename.strip()
    if not text:
        return None
    for known in _KNOWN_ARCHITECTURES:
        if text.lower().startswith(known.lower()):
            return known
    if (versioned := _VERSIONED_ARCH_RE.search(text)) is not None:
        family, major, minor = versioned.groups()
        return f"{family.upper()} {major}.{minor or 0}"
    token = re.sub(r"^\s*\d\s*[x×]\s*", "", text)
    if manufacturer != "nvidia" and re.match(r"RV200\b", token, re.IGNORECASE):
        # RV200 (Radeon 7500) is a die-shrunk R100, not R200; curated records
        # disagree on its name, so it is left out.
        return None
    table = _NVIDIA_CODENAME_ARCH if manufacturer == "nvidia" else _ATI_CODENAME_ARCH
    for pattern, arch in table:
        if re.match(rf"(?:{pattern})", token, re.IGNORECASE) or re.search(
            rf"\(({pattern})", token, re.IGNORECASE
        ):
            return arch
    return None


def classify_column(label: str) -> str | None:
    """Joined header label → field key (``None`` for columns we don't use)."""
    lab = _FOOTNOTE_RE.sub("", label).lower()
    if lab.startswith("model"):
        return "model"
    if lab.startswith("architecture"):
        return "architecture"
    if "code name" in lab or "codename" in lab:
        return "codename"
    if "bus interface" in lab:
        return "bus"
    if "launch" in lab or "release date" in lab:
        return "release_date"
    if "memory" in lab and "size" in lab:
        return "memory_size"
    if "memory" in lab and "type & width" in lab:
        return "memory_type_width"
    if "memory" in lab and "bus type" in lab:
        return "memory_type"
    # "Memory Configuration / DRAM Type", "Memory / Type".
    if "dram type" in lab or ("memory" in lab and re.search(r"/ type$", lab.strip())):
        return "memory_type"
    if "memory" in lab and "bus width" in lab:
        return "memory_bus"
    if "clock" in lab and re.search(r"\bshader\b", lab):
        return None  # "Clock Rate / Shader (Boost)" is the shader clock
    if _BASE_BOOST_RE.search(lab):
        return "base_boost"
    if "boost" in lab and "clock" in lab:
        return "boost_clock"
    if "memory" not in lab and "shader" not in lab and (
        "core clock" in lab or "core / clock" in lab or "clock rate / core" in lab
        or "base clock" in lab or "clock speed / core" in lab or "base core" in lab
        or "/ core ( mhz )" in lab
    ):
        return "base_clock"
    if "idle" in lab:
        return None
    if lab.startswith("tdp") or "board power" in lab:
        return "tdp"
    return None


def _unit(label: str) -> str:
    lab = label.lower()
    for unit in ("gib", "gb", "mib", "mb", "ghz", "mhz"):
        if re.search(rf"\b{unit}\b", lab):
            return unit
    return ""


def _single_number(text: str, *, bounds: bool = False) -> float | None:
    """The one number a cell states, or ``None`` when it states several, a
    range, an estimate (``"~75"``, ``"45?"``) or a bound. ``bounds`` accepts
    ``"Up to 512"`` and ``"<75"``, which the curated records keep as the value."""
    # A thin space separates thousands ("1 024").
    cleaned = _FOOTNOTE_RE.sub("", text).replace("\u2009", "").replace("\xa0", " ")
    if "~" in cleaned or "?" in cleaned or _RANGE_RE.search(cleaned):
        return None
    if not bounds and ("<" in cleaned or "up to" in cleaned.lower()):
        return None
    numbers = _NUMBER_RE.findall(cleaned)
    return float(numbers[0].replace(",", "")) if len(numbers) == 1 else None


def _clock_mhz(text: str, unit: str) -> int | None:
    """The one clock a cell states, in MHz."""
    value = _single_number(text)
    if value is None:
        return None
    return round(value * (1000 if unit == "ghz" or "ghz" in text.lower() else 1))


def _memory_gb(text: str, unit: str) -> float | None:
    value = _single_number(text, bounds=True)
    if value is None:
        return None
    lowered = text.lower()
    if "gb" in lowered or "gib" in lowered or unit in {"gb", "gib"}:
        return value
    if "mb" in lowered or "mib" in lowered or unit in {"mb", "mib"}:
        return mib_to_gb(value)
    return None


def _memory_type(text: str) -> str | None:
    """The memory type a cell names; ``None`` when it names several."""
    upper = text.upper()
    kinds = [
        kind
        for kind in MEMORY_TYPES
        if re.search(rf"(?<![A-Z0-9]){kind}(?![A-Z0-9])", upper)
    ]
    return _MEMORY_TYPE_DISPLAY.get(kinds[0], kinds[0]) if len(kinds) == 1 else None


def _release_period(text: str) -> tuple[date, date] | None:
    """The release period a cell names; ``None`` for several dates or a ``?``."""
    cleaned = _FOOTNOTE_RE.sub("", text)
    if "?" in cleaned:
        return None
    taken: list[tuple[int, int]] = []
    periods: set[tuple[date, date]] = set()
    for pattern in _DATE_IN_CELL_RES:
        for match in pattern.finditer(cleaned):
            if any(start < match.end() and match.start() < end for start, end in taken):
                continue
            taken.append(match.span())
            if (period := parse_date_period(match.group(0))) is not None:
                periods.add(period)
    return periods.pop() if len(periods) == 1 else None


def normalize_bus_interface(text: str) -> str | None:
    """Bus as ``"PCIe N.N xN"``: ``"PCIe 2.0 ×16"``, ``"PCIe x16 2.0"``,
    ``"PCIe 2x16"`` and ``"PCIe 2 x16"`` all give ``"PCIe 2.0 x16"``;
    ``"AGP 8×"`` → ``"AGP 8x"``.

    A cell naming two buses (``"AGP 8× PCIe ×16"``: board variants) gives
    ``None``. Several lane widths keep only the version (``"PCIe 3.0"``).
    Parenthesized remarks are dropped.
    """
    cleaned = re.sub(r"\([^()]*\)", " ", _FOOTNOTE_RE.sub("", text)).replace("×", "x")
    cleaned = re.sub(r"PCI[- ]?E\b|PCI Express", "PCIe", cleaned, flags=re.IGNORECASE)
    if len({name.lower() for name in _BUS_NAME_RE.findall(cleaned)}) > 1:
        return None
    match = _BUS_RE.search(cleaned)
    if not match:
        return None
    if match.group(1).lower() != "pcie":
        value = match.group(0)
        value = re.sub(r"\s+x\s*(\d+)", r" x\1", value)
        value = re.sub(r"(\d)\s*x\b", r"\1x", value)
        return " ".join(value.split()).strip(" -") or None
    rest = cleaned[match.end(1) :]
    lanes = {int(lane) for lane in re.findall(r"x\s*(\d{1,2})\b", rest, re.IGNORECASE)}
    versions = {
        f"{major}.{minor or 0}"
        for major, minor in re.findall(
            r"(?<![\d.])(\d)(?:\.(\d))?(?![\d.])", re.sub(r"x\s*\d{1,2}\b", " ", rest)
        )
    }
    if len(versions) > 1 or (not versions and len(lanes) > 1):
        return None
    value = "PCIe"
    if versions:
        value += f" {versions.pop()}"
    if len(lanes) == 1:
        value += f" x{lanes.pop()}"
    return value


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
            if not isinstance(table, Tag):
                continue
            labels, body = parse_table_header_block(table)
            columns = {i: (classify_column(label), _unit(label)) for i, label in enumerate(labels)}
            if not any(key == "model" for key, _ in columns.values()):
                continue
            headings = enclosing_headings(table)
            for row in body:
                cells: dict[str, tuple[str, str]] = {}
                for i, text in enumerate(row):
                    key, unit = columns.get(i, (None, ""))
                    if key and text and key not in cells:
                        cells[key] = (text, unit)
                candidate = _build_candidate(manufacturer, cells, source_url, headings)
                if candidate is not None:
                    yield candidate


def _build_candidate(
    manufacturer: str,
    cells: dict[str, tuple[str, str]],
    source_url: str,
    headings: list[str] | tuple[str, ...] = (),
) -> IngestCandidate | None:
    def text(key: str) -> str:
        return cells.get(key, ("", ""))[0]

    def unit(key: str) -> str:
        return cells.get(key, ("", ""))[1]

    raw_model = _LAPTOP_PAREN_RE.sub(" Mobile", _FOOTNOTE_RE.sub("", text("model")).strip())
    if raw_model.count("(") != raw_model.count(")"):
        return None  # "R9 M395X Amethyst XT)": a fragment of a split cell
    brand_words = [word.lower() for word in _BRAND_WORD_RE.findall(raw_model)]
    if len(brand_words) != len(set(brand_words)):
        return None  # "Radeon 530X Radeon 535": two boards in one cell
    model = _TRAILING_PAREN_RE.sub("", raw_model).strip()
    # "Radeon DDR / Radeon 7200": two names for one board; the first is the
    # launch name and is what distinguishes it (SDR vs DDR both became "7200").
    model = re.split(r"\s*/\s*", model)[0].strip()
    bus_text = text("bus")
    memory_text = " ".join(text(key) for key in ("memory_size", "memory_type"))
    if (
        not model
        or _OUT_OF_SCOPE_RE.search(model)
        or _OUT_OF_SCOPE_RE.search(bus_text)
        or _CHIPSET_BUS_RE.search(bus_text)
        or (_SYSTEM_MEMORY_RE.search(memory_text) and not _ONBOARD_RE.search(memory_text))
        or any(heading.upper().startswith("IGP") for heading in headings)
    ):
        return None
    if _MULTI_GPU_RE.search(text("memory_size")):
        # "2× 128": per-GPU values on dual-GPU boards don't fit one record.
        return None
    problems: tuple[str, ...] = ()
    if headings and headings[-1].lower().startswith("mobile") and not _LAPTOP_MARKER_RE.search(
        raw_model
    ):
        problems = ("laptop table row without a laptop marker in its name",)

    release_period = _release_period(text("release_date"))
    release_date = release_date_of(release_period)
    if manufacturer == "amd" and release_period is not None and release_period[1] < _ATI_BRAND_END:
        manufacturer = "ati"
    slug = slugify(model, manufacturer=manufacturer)
    if len(slug) < 4 or not any(ch.isdigit() for ch in slug):
        return None

    base_clock = _clock_mhz(text("base_clock"), unit("base_clock"))
    boost_clock = _clock_mhz(
        re.sub(r"\([^)]*\)", " ", text("boost_clock")), unit("boost_clock")
    )
    if "base_boost" in cells:
        combined = _FOOTNOTE_RE.sub("", text("base_boost"))
        if (pair := _BASE_BOOST_CELL_RE.fullmatch(combined)) is not None:
            base_clock = int(pair[1].replace(",", ""))
            boost_clock = int(pair[2].replace(",", ""))
        else:
            base_clock = _clock_mhz(combined, unit("base_boost"))
    if (
        boost_clock is None
        and base_clock is not None
        and "boost_clock" not in cells
        and release_date is not None
        and release_date < _BOOST_ERA
    ):
        # Pre-boost boards: the dataset stores boost == base (586/586 pre-2010).
        # Later single-clock cells are often a game clock, so they stay unknown.
        boost_clock = base_clock

    type_width = text("memory_type_width")
    memory_type = _memory_type(text("memory_type") or type_width)
    widths = set(re.findall(r"(\d+)\s*-?\s*bit", type_width, re.IGNORECASE))
    memory_bus = (
        _single_number(text("memory_bus"))
        if "memory_bus" in cells
        else (float(widths.pop()) if len(widths) == 1 else None)
    )
    tdp = _single_number(text("tdp"), bounds=True)

    architecture = architecture_from_codename(text("architecture"), manufacturer) or (
        architecture_from_codename(text("codename"), manufacturer)
    )

    brand = _BRAND_DISPLAY.get(manufacturer, manufacturer.title())
    name = model if model.lower().startswith(manufacturer) else f"{brand} {model}"
    record: dict[str, object | None] = {
        "slug": slug,
        "name": name,
        "manufacturer": manufacturer,
        "architecture": architecture,
        "release_date": release_date.isoformat() if release_date else None,
        "memory_gb": _memory_gb(text("memory_size"), unit("memory_size")),
        "memory_type": memory_type,
        "memory_bus_bit": int(memory_bus) if memory_bus else None,
        "base_clock_mhz": base_clock,
        "boost_clock_mhz": boost_clock,
        "tdp_w": math.floor(tdp + 0.5) if tdp else None,
        "pcie_version": normalize_bus_interface(bus_text),
        "msrp_usd": None,
        "verified": False,
        "source_urls": [source_url],
    }
    required = (
        "architecture", "release_date", "memory_gb", "memory_type", "memory_bus_bit",
        "base_clock_mhz", "boost_clock_mhz", "tdp_w", "pcie_version",
    )
    missing = tuple(field for field in required if record.get(field) in (None, ""))
    year = release_date.year if release_date else "unknown"
    segment = guess_gpu_segment(model)
    return IngestCandidate(
        category="gpu",
        manufacturer=manufacturer,
        slug=slug,
        record=record,
        source_url=source_url,
        output_path=Path("gpu") / manufacturer / str(year) / segment / f"{slug}.json",
        missing_fields=missing,
        problems=problems,
        released_by=release_period[1] if release_period else None,
    )
