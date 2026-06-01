"""technical.city CPU pages → legacy Cinebench scores (R15 / R10 / R11.5).

Fills the legacy Cinebench fields that PassMark's site doesn't carry. Uses
explicit per-CPU URLs (``/en/cpu/<slug>``) — no fuzzy search — and confirms the
page heading matches the requested chip. Matching is vendor-insensitive because
technical.city drops the "AMD"/"Intel" prefix ("Ryzen 7 5800X: specs and
benchmarks"). Each benchmark sits in a ``div.tab`` (``<h4>`` label) whose
``.item`` for the page's own CPU holds the value in ``<em class="avarage">``.
A field stays absent when the page doesn't list it (older chips have no R15).

Variant-safe: a wrong slug 404s or serves a different chip, which the heading
check rejects. Never fabricates.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from .passmark import normalize_name

BASE = "https://technical.city/en/cpu/{slug}"
_VENDOR_RE = re.compile(r"^(amd|intel)\s+", re.IGNORECASE)
_NUM_RE = re.compile(r"\d[\d,]*\.?\d*")


@dataclass(frozen=True)
class LegacyResult:
    page_name: str
    scores: dict[str, float]  # field name -> int|float
    source_url: str


def slug(name: str) -> str:
    """Dataset name → technical.city URL slug (drops vendor + codename)."""
    s = re.sub(r"\s*\([^)]*\)", "", name)
    s = _VENDOR_RE.sub("", s).strip()
    return re.sub(r"\s+", "-", s)


def _key(name: str) -> str:
    """Vendor-insensitive comparable key (technical.city omits the vendor)."""
    return normalize_name(_VENDOR_RE.sub("", re.sub(r"\s*\([^)]*\)", "", name)))


def _field_for(label: str) -> str | None:
    """Map a benchmark section heading to a schema field, or None."""
    low = label.lower()
    if "single" in low:
        suffix = "single"
    elif "multi" in low:
        suffix = "multi"
    else:
        return None
    if "11.5" in low:
        return f"cinebench_r11_5_{suffix}"
    if re.search(r"\br?10\b", low):
        return f"cinebench_r10_{suffix}"
    if re.search(r"\br?15\b", low):
        return f"cinebench_r15_{suffix}"
    return None


def _value(text: str, *, decimal: bool) -> float | int | None:
    m = _NUM_RE.search(text)
    if not m:
        return None
    raw = float(m.group(0).replace(",", ""))
    return raw if decimal else int(raw)


def fetch_legacy(client: httpx.Client, name: str) -> LegacyResult | None:
    """Fetch variant-confirmed legacy Cinebench scores for ``name``."""
    resp = client.get(BASE.format(slug=slug(name)))
    if resp.status_code != 200:
        return None
    soup = BeautifulSoup(resp.text, "html.parser")
    h1 = soup.find("h1")
    if h1 is None:
        return None
    heading = h1.get_text(" ", strip=True).split(":", 1)[0].strip()
    if _key(heading) != _key(name):
        return None
    # The heading gate confirms page identity; within each benchmark tab the
    # page's own CPU is the first value row (technical.city renders it as
    # "this CPU vs others"), and its <strong> may be a short form ("i9-14900K").
    scores: dict[str, float] = {}
    for tab in soup.select("div.tab"):
        h4 = tab.find("h4")
        if h4 is None:
            continue
        field = _field_for(h4.get_text(" ", strip=True))
        if field is None or field in scores:
            continue
        em = tab.select_one(".item em.avarage")
        if em is None:
            continue
        val = _value(em.get_text(" ", strip=True), decimal="r11_5" in field)
        if val is not None:
            scores[field] = val
    if not scores:
        return None
    return LegacyResult(page_name=heading, scores=scores, source_url=str(resp.url))


def resolve(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, float], str] | None:
    """Generic resolver: ``(scores_dict, source_url)`` or None (for enrich runner)."""
    r = fetch_legacy(client, name)
    return (r.scores, r.source_url) if r else None
