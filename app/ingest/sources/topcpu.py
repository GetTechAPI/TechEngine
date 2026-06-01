"""topcpu.net → CPU benchmark scores + GPU Time Spy (open static ranking pages).

topcpu.net publishes per-benchmark ranking pages where each row is an
``<input data-cmp value="<name>">`` comparison checkbox with a sibling
``span.font-bold`` score. The same parser serves every page; only the URL and
the name-normalizer differ (CPU vs GPU).

GPU: ``timespy_score`` = 3DMark Time Spy *graphics* score (GPU-only sub-score,
e.g. RTX 4090 ≈ 36 328, not the CPU-influenced overall).

CPU: fills the families our other sources leave thin/capped — Cinebench 2024
(cgdirector charts only had ~30), PassMark (cpubenchmark's public lookup caps at
~644), Geekbench 6 and Cinebench R23. Values are the same scale as our existing
sources (cross-checked: 14900K CB2024 2130 vs 2211, PassMark 61 120 vs 58 335,
GB6 22 637 vs 21 000, R23 38 497 vs 40 500 — normal cross-aggregator variance).

Bulk tables: each page fetched once, cached, matched by an exact variant-safe
normalized key (``normalize_name`` for CPUs keeps K/KF/X suffixes distinct;
``normalize_gpu`` for GPUs keeps Ti/XT/Laptop distinct). Fill-only-nulls upstream
means existing source-of-record values are never overwritten. Never fabricates.
"""

from __future__ import annotations

import re
from collections.abc import Callable

import httpx
from bs4 import BeautifulSoup

from .blender import normalize_gpu
from .passmark import normalize_name

_EN = "https://www.topcpu.net/en/"
TIMESPY_URL = _EN + "gpu-r/3dmark-time-spy"
URL = TIMESPY_URL  # back-compat: GPU Time Spy is the original single page
CPU_INDEX_URL = _EN + "cpu-r/"

# (multi_url, multi_field, single_url, single_field) per CPU benchmark family.
_CPU_FAMILIES: list[tuple[str, str, str, str]] = [
    (_EN + "cpu-r/cinebench-2024-multi-core", "cinebench_2024_multi",
     _EN + "cpu-r/cinebench-2024-single-core", "cinebench_2024_single"),
    (_EN + "cpu-r/passmark-cpu-multi-core", "passmark_cpu_mark",
     _EN + "cpu-r/passmark-cpu-single-core", "passmark_single"),
    (_EN + "cpu-r/geekbench-6-multi-core", "geekbench_multi",
     _EN + "cpu-r/geekbench-6-single-core", "geekbench_single"),
    (_EN + "cpu-r/cinebench-r23-multi-core", "cinebench_r23_multi",
     _EN + "cpu-r/cinebench-r23-single-core", "cinebench_r23_single"),
]

# (url, field, is_float) for the extra GPU benchmark dimensions.
_GPU_FAMILIES: list[tuple[str, str, bool]] = [
    (_EN + "gpu-r/3dmark-time-spy-extreme", "timespy_extreme_score", False),
    (_EN + "gpu-r/3dmark-speed-way", "speedway_score", False),
    (_EN + "gpu-r/octanebench", "octanebench_score", False),
    (_EN + "gpu-r/fp32-float", "fp32_tflops", True),
]

_BOLD = re.compile(r"font-bold")
_DIGITS = re.compile(r"[^0-9]")
_NUM = re.compile(r"[\d,]+\.?\d*")

# Cached normalized score maps, keyed by (url, normalizer name).
_caches: dict[str, dict[str, float]] = {}


def _load_map(
    client: httpx.Client,
    url: str,
    normalizer: Callable[[str], str],
    *,
    as_float: bool = False,
) -> dict[str, float]:
    ckey = f"{url}|{normalizer.__name__}"
    if ckey in _caches:
        return _caches[ckey]
    table: dict[str, float] = {}
    _caches[ckey] = table
    resp = client.get(url)
    if resp.status_code != 200:
        return table
    soup = BeautifulSoup(resp.text, "html.parser")
    for inp in soup.select("input[data-cmp]"):
        name = inp.get("value")
        row = inp.parent
        if not isinstance(name, str) or not name or row is None:
            continue
        bold = row.find("span", class_=_BOLD)
        if bold is None:
            continue
        text = bold.get_text(strip=True)
        if as_float:
            m = _NUM.search(text)
            value: float | None = float(m.group(0).replace(",", "")) if m else None
        else:
            digits = _DIGITS.sub("", text)
            value = int(digits) if digits else None
        if value is None:
            continue
        key = normalizer(name)
        if key:
            # First occurrence wins (page is sorted best-first).
            table.setdefault(key, value)
    return table


def reset_cache() -> None:
    """Clear module caches (tests / re-runs)."""
    _caches.clear()


def resolve(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, int], str] | None:
    """GPU Time Spy resolver: ``({"timespy_score": score}, url)`` or None."""
    hit = _load_map(client, TIMESPY_URL, normalize_gpu).get(normalize_gpu(name))
    if hit is None:
        return None
    return {"timespy_score": int(hit)}, TIMESPY_URL


def resolve_cpu(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, int], str] | None:
    """CPU resolver: fills any of the four families present, or None."""
    key = normalize_name(name)
    if not key:
        return None
    scores: dict[str, int] = {}
    for multi_url, multi_field, single_url, single_field in _CPU_FAMILIES:
        m = _load_map(client, multi_url, normalize_name).get(key)
        if m is not None:
            scores[multi_field] = int(m)
        s = _load_map(client, single_url, normalize_name).get(key)
        if s is not None:
            scores[single_field] = int(s)
    if not scores:
        return None
    return scores, CPU_INDEX_URL


def resolve_gpu(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, float], str] | None:
    """GPU breadth resolver: Time Spy Extreme / Speed Way / OctaneBench / FP32.

    WARNING: topcpu publishes unreliable *estimated* 3DMark/Octane scores for
    pre-DX12 cards that can't actually run them (e.g. Radeon HD 5670 "Time Spy"
    3897 — physically impossible; contradicts its PassMark G3D). The same applies
    to ``resolve`` (Time Spy). When enriching, GUARD on DX12 capability
    (release year >= 2011 / GCN/Kepler+) before writing timespy*/speedway/
    octanebench — only fp32_tflops (a spec) is era-safe. See
    TechAPI/.claude/benchmark_fill_progress.md pt.7.
    """
    key = normalize_gpu(name)
    if not key:
        return None
    scores: dict[str, float] = {}
    for url, field, as_float in _GPU_FAMILIES:
        v = _load_map(client, url, normalize_gpu, as_float=as_float).get(key)
        if v is not None:
            scores[field] = v
    if not scores:
        return None
    return scores, CPU_INDEX_URL.replace("cpu-r", "gpu-r")
