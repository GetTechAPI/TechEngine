"""Wikipedia CPU list pages → ``IngestCandidate`` rows.

Each ``List_of_<vendor>_<family>_processors`` page on Wikipedia is a series
of ``table.wikitable`` blocks; rows are individual SKUs and columns map to
schema fields. Header text varies subtly between pages, so we match by
loose keywords (``"cores"``, ``"base"``, ``"tdp"`` …) rather than position.
Rows whose first column is provided via ``rowspan`` on the preceding row
(e.g. an ``Architecture`` cell shared across a generation) are materialised
by the shared grid parser.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

from bs4 import BeautifulSoup, Tag

from app.coverage.normalize import slugify
from app.coverage.sources.wikipedia import fetch_wikipedia_html

from ..normalize import (
    guess_cpu_segment,
    parse_cache_mb,
    parse_cores_threads,
    parse_date_period,
    parse_frequency_ghz,
    parse_frequency_range_ghz,
    parse_int,
    parse_tdp_w,
)
from .base import IngestCandidate
from .cpu_tables import parse_cpu_table

# (manufacturer, page, product family for bare model numbers). Architecture
# comes from an explicit column or a preceding section, never this family.
PAGES: list[tuple[str, str, str]] = [
    ("intel", "List_of_Intel_Core_processors", "Intel Core"),
    ("intel", "List_of_Intel_Xeon_processors", "Intel Xeon"),
    ("intel", "List_of_Intel_Atom_processors", "Intel Atom"),
    ("amd", "List_of_AMD_Ryzen_processors", "AMD Ryzen"),
    ("amd", "List_of_AMD_Opteron_processors", "AMD Opteron"),
    ("amd", "List_of_AMD_Epyc_processors", "AMD EPYC"),
    ("amd", "List_of_AMD_Threadripper_processors", "AMD Threadripper"),
]

# Citation markers left in model cells ("7501 [ 32 ] [ 33 ]").
_FOOTNOTE_RE = re.compile(r"\s*\[\s*[\w\s]{1,6}\]")
# Rows naming only a family tier ("Ryzen 5", "Core i7", "Xeon 6") are group
# headers, not SKUs; they pass the has-a-digit check but must never become records.
_FAMILY_ONLY_RE = re.compile(
    r"(?:ryzen|ryzen-pro|core|core-ultra|core-i|xeon|epyc|athlon|pentium|celeron|atom|opteron)"
    r"-?\d{1,2}"
)

# Manufacturer keys are stored lowercase; these are their display forms used to
# synthesize ``name`` when the model string omits the brand. Plain ``.upper()``
# mangles "intel" → "INTEL" (an ingest casing artifact); AMD is genuinely
# all-caps so it gets an explicit entry rather than title-casing.
_BRAND_DISPLAY: dict[str, str] = {"intel": "Intel", "amd": "AMD"}


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
            section_label = _nearest_section_label(table) or ""
            for row in parse_cpu_table(table):
                model = _FOOTNOTE_RE.sub("", row.get("model", "")).strip()
                family = row.get("family", "")
                if family and not model.lower().startswith(family.lower()):
                    model = f"{family} {model}"
                elif not family and re.fullmatch(
                    r"[A-Za-z0-9]*\d[A-Za-z0-9]*(?:\s+(?:HE|EE|SE))?", model
                ):
                    # Bare SKUs use the product family explicitly named by the page.
                    page_family = fallback_arch
                    section = table.find_previous("h2")
                    if section and "embedded" in section.get_text().lower():
                        page_family += " Embedded"
                    model = f"{page_family} {model}"
                slug = slugify(model, manufacturer=manufacturer)
                if len(slug) < 4 or not any(ch.isdigit() for ch in slug):
                    continue
                if _FAMILY_ONLY_RE.fullmatch(slug):
                    continue
                architecture = row.get("architecture") or architecture_from_heading(
                    section_label
                )
                # Parenthesized PRO denotes both ordinary and PRO models in one row.
                models = [model]
                if re.search(r"\(\s*PRO\s*\)", model):
                    models = [
                        re.sub(r"\(\s*PRO\s*\)", replacement, model) for replacement in ("", "PRO")
                    ]
                for variant in models:
                    variant = " ".join(variant.split())
                    yield _build_candidate(
                        manufacturer=manufacturer,
                        architecture=architecture,
                        model=variant,
                        slug=slugify(variant, manufacturer=manufacturer),
                        row=row,
                        source_url=source_url,
                        heading=section_label,
                    )


_HEADING_NODE_RE = re.compile(r"\((\d+(?:\.\d+)?)\s*nm\)")


def _clean_architecture(label: str) -> tuple[str, str | None]:
    """``'" Denverton " (14 nm)'`` → ``("Denverton", "14 nm")``.

    Section headings on the Atom/Core list pages quote the codename and carry
    the node in parentheses; the raw heading text leaked both into
    ``architecture``.
    """
    node_match = _HEADING_NODE_RE.search(label)
    node = f"{node_match.group(1)} nm" if node_match else None
    name = _HEADING_NODE_RE.sub("", label).replace('"', "").replace("“", "").replace("”", "")
    return " ".join(name.split()) or label, node


# Headings that name a market or a generation, not a microarchitecture.
_GENERIC_HEADING_RE = re.compile(
    r"\b(?:mobile|desktop|server|embedded|iot|processors?|generation|series|workstation|"
    r"mainstream|laptop|notebook|based)\b",
    re.IGNORECASE,
)
_CODENAMED_RE = re.compile(r"\(\s*codenamed\s+([^()]+?)\s*\)", re.IGNORECASE)
_CODENAME_RE = re.compile(r"[A-Z][A-Za-z]*(?:[ -][A-Z][A-Za-z]*)*(?:-[A-Z0-9]{1,2})?")
_ZEN_RE = re.compile(r"\b(Zen(?: \d\+?c?)?\+?)(?=[/ ,)]|$)")


def architecture_from_heading(label: str) -> str:
    """Architecture named by a section heading, or ``""`` when it names none.

    ``'"Denverton" (14 nm)'`` → ``Denverton``; ``"Desktop (codenamed Comet Lake )"``
    → ``Comet Lake``; ``"Raphael (7000 series, Zen 4/RDNA2 based)"`` → ``Zen 4``.
    ``"Mobile"``, ``"Fourth generation Epyc (Genoa, Bergamo and Siena)"`` and other
    market or generation headings name no single architecture, so they give
    ``""`` and the row is skipped rather than labelled with the heading.
    """
    if not label:
        return ""
    name, _node = _clean_architecture(label)
    if (codenamed := _CODENAMED_RE.search(name)) is not None:
        codename = " ".join(codenamed.group(1).split())
        return codename if _CODENAME_RE.fullmatch(codename) else ""
    if (zen := _ZEN_RE.findall(name)) and len(set(zen)) == 1:
        return str(zen[0])
    # "Raptor Lake (13th gen)": the codename is outside the parentheses.
    name = " ".join(re.sub(r"\([^()]*\)", " ", name).split())
    if _GENERIC_HEADING_RE.search(name):
        return ""
    return name if _CODENAME_RE.fullmatch(name) else ""


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
    heading: str = "",
) -> IngestCandidate:
    cores_field = row.get("cores", "")
    threads_field = row.get("threads", "")
    cores, threads_via_slash = parse_cores_threads(cores_field)
    threads = parse_int(threads_field) or threads_via_slash

    release_period = parse_date_period(row.get("release_date", ""))
    release_date = release_period[0] if release_period else None
    base_clock = parse_frequency_ghz(row.get("base_clock", ""))
    boost_clock = parse_frequency_ghz(row.get("boost_clock", ""))
    # "1.7–2.0 GHz" in a single frequency cell is base–boost; the plain parser
    # would read only the number glued to the unit (2.0) as the base clock.
    if (clock_range := parse_frequency_range_ghz(row.get("base_clock", ""))) is not None:
        base_clock = clock_range[0]
        boost_clock = boost_clock or clock_range[1]
    l3_cache = parse_cache_mb(row.get("l3_cache", ""))
    tdp = parse_tdp_w(row.get("tdp", ""))
    socket = row.get("socket") or None
    process_node = row.get("process_node") or None
    architecture, node_from_heading = _clean_architecture(architecture)
    if not row.get("architecture"):
        # The architecture came from the section heading; so may the node.
        node_from_heading = _clean_architecture(heading)[1]
    process_node = process_node or node_from_heading

    segment = guess_cpu_segment(model)
    brand = _BRAND_DISPLAY.get(manufacturer, manufacturer.title())
    name = model if model.lower().startswith(manufacturer) else f"{brand} {model}"

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
    problems = (row["cores_note"],) if row.get("cores_note") else ()

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
        problems=problems,
        released_by=release_period[1] if release_period else None,
    )
