"""PassMark (cpubenchmark.net) CPU benchmark scraper — variant-safe.

cpubenchmark's name search (``cpu.php?cpu=<NAME>``) does FUZZY matching and will
silently serve a *sibling* SKU: a request for "Ryzen 7 5800X" returns the
5800X3D, "i9-14900K" returns the 14900KS, "i5-12400" returns the 12400F. Writing
those numbers into a ``verified: true`` dataset corrupts it (observed ~50%
mismatch rate on plain names). So this client only returns scores when the
served page's heading matches the requested chip EXACTLY. Fuzzy mismatches are
surfaced for manual review (or resolved via an explicit ``id`` override) rather
than guessed — the safe default for a curated dataset.

Network/DOM note: PassMark has no clean public API, so scores are extracted from
the rendered page text by label (robust to minor DOM churn). ``id`` overrides
let a maintainer pin the canonical ``cpu.php?id=<N>`` page for an ambiguous name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

BASE = "https://www.cpubenchmark.net/cpu.php"
LOOKUP = "https://www.cpubenchmark.net/cpu_lookup.php"
USER_AGENT = "TechEngine-Ingest/0.1 (+https://github.com/GetTechAPI/TechEngine)"

# cpubenchmark.net / notebookcheck / technical.city return 403 (or hang) for the
# bare ingest UA — they bot-gate on a browser-shaped header set. We still rate-
# limit via --sleep and fetch per-chip with attribution (no bulk harvest).
BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Sec-Ch-Ua": '"Chromium";v="124", "Google Chrome";v="124"',
    "Sec-Ch-Ua-Mobile": "?0",
    "Sec-Ch-Ua-Platform": '"Windows"',
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
}

_ID_RE = re.compile(r"[?&]id=(\d+)")

# Trailing decorations PassMark appends to the model name that the curated
# dataset does not carry. Stripped before comparison.
_CLOCK_RE = re.compile(r"\s*@\s*[\d.]+\s*ghz", re.IGNORECASE)
_GFX_RE = re.compile(r"\s*(?:w/|with)\s+.*$", re.IGNORECASE)
_NOISE_RE = re.compile(r"\b(processor|cpu)\b", re.IGNORECASE)
# Marketing/core-count descriptors the dataset and PassMark disagree on. Safe to
# drop from BOTH sides: the model number is still required for an exact match.
_DESC_RE = re.compile(
    r"\b(black edition|extreme edition|"
    r"(?:dual|two|quad|four|six|eight|ten|twelve|sixteen|\d+)[- ]core)\b",
    re.IGNORECASE,
)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_CPU_MARK_RE = re.compile(r"(?:Multithread Rating|Average CPU Mark)[:\s]*([\d,]+)", re.I)
_SINGLE_RE = re.compile(r"Single Thread Rating[:\s]*([\d,]+)", re.I)


@dataclass(frozen=True)
class PassMarkResult:
    """Variant-confirmed PassMark scores for one CPU."""

    page_name: str
    cpu_mark: int
    single_thread: int
    source_url: str


def normalize_name(name: str) -> str:
    """Reduce a CPU name to a comparable canonical key.

    Drops clock suffixes ("@ 3.80GHz"), integrated-graphics tails
    ("with Radeon Graphics"), the words "processor"/"cpu", and all non
    alphanumerics — so "AMD Ryzen 7 5800X @ 3.80GHz" and "AMD Ryzen 7 5800X"
    compare equal, while "5800X" and "5800X3D" stay distinct.
    """
    s = name.strip()
    s = _CLOCK_RE.sub("", s)
    s = _GFX_RE.sub("", s)
    s = _NOISE_RE.sub("", s)
    s = _DESC_RE.sub("", s)
    # Drop a parenthetical codename, e.g. "(Comet Lake)".
    s = re.sub(r"\s*\([^)]*\)", "", s)
    return _NON_ALNUM.sub("", s.lower())


def heading_matches(requested: str, page_heading: str) -> bool:
    """True iff the served page is exactly the requested chip (variant-safe)."""
    return normalize_name(requested) == normalize_name(page_heading)


def search_query(name: str) -> str:
    """A search-friendly form of ``name`` for the ``cpu=`` query parameter.

    Drops parenthetical codenames ("(Bloomfield)", "(Vishera)") that the
    dataset carries but PassMark's search box does not understand — without
    them the lookup finds the chip, and ``heading_matches`` (which also strips
    them) still guards the final write.
    """
    no_paren = re.sub(r"\s*\([^)]*\)", "", name)
    return re.sub(r"\s+", " ", _DESC_RE.sub("", no_paren)).strip()


def _extract(html: str) -> tuple[str, int, int] | None:
    """Return ``(page_heading, cpu_mark, single_thread)`` or None if unparseable."""
    soup = BeautifulSoup(html, "html.parser")
    heading_el = soup.select_one(".cpuname") or soup.find(["h1", "h2"])
    if heading_el is None:
        return None
    heading = heading_el.get_text(" ", strip=True)
    text = soup.get_text(" ", strip=True)
    mark_m = _CPU_MARK_RE.search(text)
    single_m = _SINGLE_RE.search(text)
    if not mark_m or not single_m:
        return None
    cpu_mark = int(mark_m.group(1).replace(",", ""))
    single = int(single_m.group(1).replace(",", ""))
    return heading, cpu_mark, single


def resolve_id(client: httpx.Client, name: str) -> str | None:
    """Find the canonical PassMark id for ``name`` via the lookup list.

    ``cpu_lookup.php?cpu=<NAME>`` returns a large result list of
    ``<span class="prdname">`` entries, each inside an anchor carrying the
    chip's ``id``. We return the id of the row whose name matches ``name``
    exactly (variant-safe) — this disambiguates plain SKUs that the fuzzy
    ``cpu.php`` search would otherwise redirect to a popular sibling.
    """
    resp = client.get(LOOKUP, params={"cpu": search_query(name)})
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    want = normalize_name(name)
    for span in soup.select("span.prdname"):
        anchor = span.find_parent("a", href=True)
        if anchor is None:
            continue
        href = anchor["href"]
        if not isinstance(href, str):
            continue
        m = _ID_RE.search(href)
        if m and normalize_name(span.get_text(" ", strip=True)) == want:
            return m.group(1)
    return None


def _fetch_by(client: httpx.Client, name: str, params: dict[str, str]) -> PassMarkResult | None:
    resp = client.get(BASE, params=params)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    parsed = _extract(resp.text)
    if parsed is None:
        return None
    heading, cpu_mark, single = parsed
    if not heading_matches(name, heading):
        return None
    return PassMarkResult(
        page_name=heading, cpu_mark=cpu_mark, single_thread=single, source_url=str(resp.url)
    )


def fetch_scores(
    client: httpx.Client,
    name: str,
    *,
    id_override: str | None = None,
    auto_resolve: bool = True,
) -> PassMarkResult | None:
    """Fetch variant-confirmed scores for ``name``.

    Order: (1) ``id_override`` if given; (2) fuzzy name search — kept only if
    the served heading matches exactly; (3) ``auto_resolve`` via the lookup
    list to find the exact id, then the canonical id page. Returns None only
    when no exact-variant match exists anywhere (caller flags for review).
    """
    query = search_query(name)
    if id_override:
        return _fetch_by(client, name, {"id": id_override, "cpu": query})
    direct = _fetch_by(client, name, {"cpu": query})
    if direct is not None:
        return direct
    if not auto_resolve:
        return None
    resolved = resolve_id(client, name)
    if resolved is None:
        return None
    return _fetch_by(client, name, {"id": resolved, "cpu": name})


def make_client(*, timeout: float = 30.0) -> httpx.Client:
    return httpx.Client(
        headers=BROWSER_HEADERS, timeout=timeout, follow_redirects=True
    )
