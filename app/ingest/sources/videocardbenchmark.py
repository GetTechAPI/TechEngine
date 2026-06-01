"""videocardbenchmark.net → passmark_g3d_mark (PassMark G3D Mark, GPU).

PassMark's GPU database is the GPU analogue of cpubenchmark.net. Its
``gpu_list.php`` page is one big HTML table covering ~the entire history of
discrete GPUs — modern RTX/RX down to GeForce 256, Voodoo and Matrox — so unlike
Blender/Time Spy (which only test ~2014+ cards) it can fill the legacy GPUs.

Each row is ``<TR id="gpuNNNN"><TD><A ...>NAME</A></TD><TD>G3D</TD>…``. Bulk
table: fetched once, cached, matched by exact ``normalize_gpu`` key (variant-safe
— RTX 4070 ≠ 4070 Ti). ToS: per-name lookup + attribution, no bulk re-publishing
of the chart. Never fabricates — an unlisted GPU stays null.
"""

from __future__ import annotations

import re

import httpx
from bs4 import BeautifulSoup

from .blender import normalize_gpu

URL = "https://www.videocardbenchmark.net/gpu_list.php"
_DIGITS = re.compile(r"[^0-9]")

_cache: dict[str, int] = {}


def _load(client: httpx.Client) -> dict[str, int]:
    if _cache:
        return _cache
    resp = client.get(URL)
    if resp.status_code != 200:
        return _cache
    soup = BeautifulSoup(resp.text, "html.parser")
    for tr in soup.select('tr[id^="gpu"]'):
        cells = tr.find_all("td")
        if len(cells) < 2:
            continue
        name = cells[0].get_text(" ", strip=True)
        digits = _DIGITS.sub("", cells[1].get_text())
        if not name or not digits:
            continue
        key = normalize_gpu(name)
        if key:
            _cache.setdefault(key, int(digits))
    return _cache


def reset_cache() -> None:
    """Clear module cache (tests / re-runs)."""
    _cache.clear()


def resolve(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, int], str] | None:
    """PassMark G3D resolver: ``({"passmark_g3d_mark": score}, url)`` or None."""
    hit = _load(client).get(normalize_gpu(name))
    if hit is None:
        return None
    return {"passmark_g3d_mark": hit}, URL
