"""cgdirector.com Cinebench charts → R23 and Cinebench-2024 scores (bulk tables).

Two static chart pages (R23 ~80 CPUs; Cinebench 2024 ~50 CPUs), each listing
CPU + single + multi. Unlike the per-CPU sources these are *bulk tables*: each
page is fetched once, cached, and matched by exact normalized name (variant-safe
— "7900X" ≠ "7900X3D"). technical.city/notebookcheck have no Cinebench 2024 and
the per-CPU R23/2024 aggregators (cpu-monkey, nanoreview) block bots, so these
charts are the fetchable Cinebench-2024 / extra-R23 source. Never fabricates.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .passmark import normalize_name

R23_URL = "https://www.cgdirector.com/cinebench-r23-scores-updated-results/"
CB2024_URL = "https://www.cgdirector.com/cinebench-2024-scores/"

_caches: dict[str, dict[str, tuple[int, int]]] = {}


def _num(text: str) -> int | None:
    digits = re.sub(r"[^\d]", "", text)
    return int(digits) if digits else None


def _load(client: httpx.Client, url: str) -> dict[str, tuple[int, int]]:
    if url in _caches:
        return _caches[url]
    table_data: dict[str, tuple[int, int]] = {}
    _caches[url] = table_data
    resp = client.get(url)
    if resp.status_code != 200:
        return table_data
    soup = BeautifulSoup(resp.text, "html.parser")
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 3:
            continue
        header = [c.get_text(" ", strip=True).lower() for c in rows[0].find_all(["th", "td"])]
        try:
            ni = next(i for i, h in enumerate(header) if "name" in h)
            si = next(i for i, h in enumerate(header) if "single" in h)
            mi = next(i for i, h in enumerate(header) if "multi" in h)
        except StopIteration:
            continue
        for tr in rows[1:]:
            cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
            if len(cells) <= max(ni, si, mi):
                continue
            single, multi = _num(cells[si]), _num(cells[mi])
            key = normalize_name(cells[ni])
            if key and single and multi:
                table_data[key] = (single, multi)
    return table_data


def reset_cache() -> None:
    """Clear the module caches (tests / re-runs)."""
    _caches.clear()


def _resolve(
    client: httpx.Client, name: str, url: str, prefix: str
) -> tuple[dict[str, int], str] | None:
    hit = _load(client, url).get(normalize_name(name))
    if hit is None:
        return None
    single, multi = hit
    return {f"{prefix}_single": single, f"{prefix}_multi": multi}, url


def resolve(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, int], str] | None:
    """Cinebench R23 resolver: ``(scores_dict, source_url)`` or None."""
    return _resolve(client, name, R23_URL, "cinebench_r23")


def resolve_2024(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, int], str] | None:
    """Cinebench 2024 resolver: ``(scores_dict, source_url)`` or None."""
    return _resolve(client, name, CB2024_URL, "cinebench_2024")
