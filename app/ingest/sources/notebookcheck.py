"""notebookcheck.net Mobile-Processors Benchmark List → Cinebench R15 + R23.

One large static table (~1,276 CPUs, desktop + mobile) with columns for
Cinebench R15 single/multi and R23 single/multi (averaged review values, hence
decimals + an "n<count>" sample annotation). Far broader than cgdirector and
covers mobile parts the other sources lack. Fetched once, cached; matched by
exact normalized name (variant-safe). Columns are located by header text, not
position. Fills only the fields present for a chip; never fabricates.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .passmark import normalize_name

URL = "https://www.notebookcheck.net/Mobile-Processors-Benchmark-List.2436.0.html"

_cache: dict[str, dict[str, int]] | None = None


def _col_field(header: str) -> str | None:
    """Map a normalized header to a schema field (substring match, robust to
    extra tokens like '64Bit'). Takes Cinebench R15/R23 and Geekbench 6 columns.
    Geekbench 6.x only (matches the dataset's GB6 column) — GB5.5 is ignored."""
    side = "single" if "single" in header else "multi" if "multi" in header else None
    if side is None:
        return None
    if "cinebench" in header:
        ver = "r15" if "r15" in header else "r23" if "r23" in header else None
        return f"cinebench_{ver}_{side}" if ver else None
    if "geekbench6" in header:  # GB6.x e.g. "geekbench66singlecore"
        return f"geekbench_{side}"
    return None


def _num(text: str) -> float | None:
    m = re.search(r"\d[\d,]*\.?\d*", text)
    return float(m.group(0).replace(",", "")) if m else None


def _norm_head(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _load(client: httpx.Client) -> dict[str, dict[str, int]]:
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    resp = client.get(URL)
    if resp.status_code != 200:
        return _cache
    table = BeautifulSoup(resp.text, "html.parser").find("table")
    if table is None:
        return _cache
    rows = table.find_all("tr")
    if not rows:
        return _cache
    header = [_norm_head(c.get_text(" ", strip=True)) for c in rows[0].find_all(["th", "td"])]
    model_idx = next((i for i, h in enumerate(header) if h == "model"), 1)
    col_map = {i: f for i, h in enumerate(header) if (f := _col_field(h))}
    if not col_map:
        return _cache
    for tr in rows[1:]:
        cells = tr.find_all(["td", "th"])
        if len(cells) <= model_idx:
            continue
        name = cells[model_idx].get_text(" ", strip=True)
        if not name:
            continue
        scores: dict[str, int] = {}
        for idx, field in col_map.items():
            if idx >= len(cells):
                continue
            val = _num(cells[idx].get_text(" ", strip=True))
            if val is not None and val > 0:
                scores[field] = int(round(val))  # R15/R23 stored as ints
        if scores:
            _cache.setdefault(normalize_name(name), scores)
    return _cache


def reset_cache() -> None:
    global _cache
    _cache = None


def _subset(
    client: httpx.Client, name: str, prefix: str
) -> tuple[dict[str, int], str] | None:
    hit = _load(client).get(normalize_name(name))
    if not hit:
        return None
    picked = {k: v for k, v in hit.items() if k.startswith(prefix)}
    return (picked, URL) if picked else None


def resolve(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, int], str] | None:
    """Cinebench R15/R23 resolver: ``(scores_dict, source_url)`` or None."""
    return _subset(client, name, "cinebench")


def resolve_geekbench(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, int], str] | None:
    """Geekbench 6 resolver: ``(scores_dict, source_url)`` or None."""
    return _subset(client, name, "geekbench")
