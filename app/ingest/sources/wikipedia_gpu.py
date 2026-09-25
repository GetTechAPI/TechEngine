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

from ..normalize import guess_gpu_segment, parse_date
from .base import IngestCandidate
from .wikitable import parse_table_header_block

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
# Integrated/chipset graphics are out of scope for the discrete-GPU table.
_OUT_OF_SCOPE_RE = re.compile(r"\bIGP\b|integrated|on-?die|\bAPU\b|nForce", re.IGNORECASE)

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
    if "memory" in lab and "bus width" in lab:
        return "memory_bus"
    if "boost" in lab and "clock" in lab:
        return "boost_clock"
    if "memory" not in lab and "shader" not in lab and (
        "core clock" in lab or "core / clock" in lab or "clock rate / core" in lab
        or "base clock" in lab
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


def _number(text: str) -> float | None:
    match = _NUMBER_RE.search(text.replace(" ", "").replace("\xa0", " "))
    return float(match.group(0).replace(",", "")) if match else None


def _clock_mhz(text: str, unit: str) -> tuple[int | None, int | None]:
    """Cell → ``(base, boost)`` MHz. ``"500–700"`` is a base–boost range."""
    scale = 1000 if unit == "ghz" or "ghz" in text.lower() else 1
    if (rng := _RANGE_RE.search(text)) is not None:
        low = float(rng.group(1).replace(",", "")) * scale
        high = float(rng.group(2).replace(",", "")) * scale
        if low < high:
            return round(low), round(high)
    value = _number(text)
    return (round(value * scale), None) if value is not None else (None, None)


def _memory_gb(text: str, unit: str) -> float | None:
    value = _number(text)
    if value is None:
        return None
    lowered = text.lower()
    if "gb" in lowered or "gib" in lowered or unit in {"gb", "gib"}:
        return value
    if "mb" in lowered or "mib" in lowered or unit in {"mb", "mib"}:
        return round(value / 1024, 4)
    return None


def _memory_type(text: str) -> str | None:
    upper = text.upper()
    for kind in MEMORY_TYPES:
        if re.search(rf"(?<![A-Z0-9]){kind}(?![A-Z0-9])", upper):
            return _MEMORY_TYPE_DISPLAY.get(kind, kind)
    return None


def normalize_bus_interface(text: str) -> str | None:
    """``"PCIe 2.0 ×16"`` → ``"PCIe 2.0 x16"``; ``"AGP 8×"`` → ``"AGP 8x"``."""
    match = _BUS_RE.search(_FOOTNOTE_RE.sub("", text))
    if not match:
        return None
    value = match.group(0).replace("×", "x").replace("PCI-E", "PCIe").replace("PCI Express", "PCIe")
    # "AGP 4× PCI" lists alternative board variants; keep the first bus only.
    first, *rest = re.split(r"\s(?=(?:PCIe|AGP|PCI|MXM)\b)", value, maxsplit=1)
    value = first if rest else value
    value = re.sub(r"\s+x\s*(\d+)", r" x\1", value)
    value = re.sub(r"(\d)\s*x\b", r"\1x", value)
    return " ".join(value.split()).strip(" -") or None


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
            for row in body:
                cells: dict[str, tuple[str, str]] = {}
                for i, text in enumerate(row):
                    key, unit = columns.get(i, (None, ""))
                    if key and text and key not in cells:
                        cells[key] = (text, unit)
                candidate = _build_candidate(manufacturer, cells, source_url)
                if candidate is not None:
                    yield candidate


def _build_candidate(
    manufacturer: str, cells: dict[str, tuple[str, str]], source_url: str
) -> IngestCandidate | None:
    def text(key: str) -> str:
        return cells.get(key, ("", ""))[0]

    def unit(key: str) -> str:
        return cells.get(key, ("", ""))[1]

    raw_model = _FOOTNOTE_RE.sub("", text("model")).strip()
    model = _TRAILING_PAREN_RE.sub("", raw_model).strip()
    # "Radeon DDR / Radeon 7200": two names for one board; the first is the
    # launch name and is what distinguishes it (SDR vs DDR both became "7200").
    model = model.split(" / ")[0].strip()
    bus_text = text("bus")
    if not model or _OUT_OF_SCOPE_RE.search(model) or _OUT_OF_SCOPE_RE.search(bus_text):
        return None
    if _MULTI_GPU_RE.search(text("memory_size")):
        # "2× 128": per-GPU values on dual-GPU boards don't fit one record.
        return None

    release_date = parse_date(text("release_date"))
    if manufacturer == "amd" and release_date is not None and release_date < _ATI_BRAND_END:
        manufacturer = "ati"
    slug = slugify(model, manufacturer=manufacturer)
    if len(slug) < 4 or not any(ch.isdigit() for ch in slug):
        return None

    base_clock, range_boost = _clock_mhz(text("base_clock"), unit("base_clock"))
    unparenthesized = re.sub(r"\([^)]*\)", "", text("base_clock"))
    if range_boost is None and len(re.findall(r"\d+", unparenthesized)) > 1:
        # RDNA/Polaris "Core / Clock" cells stack game + boost ("1855 2495"): no base.
        base_clock = None
    boost_clock = _clock_mhz(text("boost_clock"), unit("boost_clock"))[0] or range_boost
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
    width_match = re.search(r"(\d+)\s*-?\s*bit", type_width, re.IGNORECASE)
    memory_bus = _number(text("memory_bus")) or (
        float(width_match.group(1)) if width_match else None
    )
    tdp = _number(text("tdp"))

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
    )
