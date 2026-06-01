"""opendata.blender.org → blender_score (Blender Benchmark, GPU).

The Blender Open Data project publishes every benchmark submission as one big
CC0 JSONL snapshot (~100 MB). Each submission line carries a ``data`` list with
one entry per scene; since Blender 3.0 the official *score* for a run is the sum
of ``samples_per_minute`` across the three standard scenes (monster, junkshop,
classroom), and a device's headline score is the **median** of that sum across
all its runs — which is exactly what the website charts show.

Scores differ between Blender major versions, so we pin to a single version
(default 4.5, the release with the most GPU submissions) for cross-GPU
comparability — the same version-alignment rule used for Geekbench. Only GPU
device types are kept (OPTIX/CUDA/HIP/METAL/ONEAPI); CPU rows are ignored.

Like the other bulk sources this is fetched once, cached, and matched by exact
normalized device name (variant-safe — "RTX 4070" never matches "RTX 4070 Ti").
Never fabricates: a GPU with no run at the pinned version stays null.
"""

from __future__ import annotations

import io
import re
import statistics
import zipfile

import httpx

SNAPSHOT_URL = "https://opendata.blender.org/snapshots/opendata-latest.zip"
DEFAULT_VERSION = "4.5"
_GPU_TYPES = {"OPTIX", "CUDA", "HIP", "METAL", "ONEAPI"}

# Tokens that never disambiguate a GPU model — dropped before matching so the
# vendor-prefixed Blender name ("NVIDIA GeForce RTX 4070") and our vendorless
# dataset name ("GeForce RTX 4070") collapse to the same key. Model-line tokens
# (rtx/gtx/rx/arc) and suffixes (ti/super/xt/xtx) are kept — they're identity.
_DROP = re.compile(
    r"\b(nvidia|amd|ati|intel|geforce|radeon|graphics|gpu|series|edition)\b",
    re.IGNORECASE,
)
_MEM = re.compile(r"\b\d+\s*gb\b", re.IGNORECASE)
_PAREN = re.compile(r"\s*\([^)]*\)")
_OGL_TAIL = re.compile(r"/.*$")  # "RTX 3070/PCIe/SSE2" -> "RTX 3070"
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

_cache: dict[str, dict[str, float]] = {}


def normalize_gpu(name: str) -> str:
    """Reduce a GPU name to a comparable key (vendor/marketing/memory-insensitive)."""
    s = _PAREN.sub("", name)
    s = _OGL_TAIL.sub("", s)
    s = _MEM.sub("", s)
    s = _DROP.sub(" ", s)
    return _NON_ALNUM.sub("", s.lower())


def _parse(raw: bytes, version: str) -> dict[str, float]:
    """Build ``{normalized_device: median_score}`` for the pinned version."""
    import json

    runs: dict[str, list[float]] = {}
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        data = rec.get("data") if isinstance(rec, dict) else None
        if not isinstance(data, list) or not data:
            continue
        first = data[0]
        if not isinstance(first, dict):
            continue
        if not first.get("blender_version", {}).get("version", "").startswith(version):
            continue
        if first.get("device_info", {}).get("device_type") not in _GPU_TYPES:
            continue
        devices = first.get("device_info", {}).get("compute_devices", [])
        if not devices:
            continue
        name = devices[0].get("name", "")
        total = 0.0
        for entry in data:
            if not isinstance(entry, dict):
                total = 0.0
                break
            spm = entry.get("stats", {}).get("samples_per_minute")
            if not isinstance(spm, (int, float)):
                total = 0.0
                break
            total += spm
        if total <= 0:
            continue
        key = normalize_gpu(name)
        if key:
            runs.setdefault(key, []).append(total)
    return {k: round(statistics.median(v), 2) for k, v in runs.items()}


def _load(client: httpx.Client, version: str) -> dict[str, float]:
    if version in _cache:
        return _cache[version]
    table: dict[str, float] = {}
    _cache[version] = table
    resp = client.get(SNAPSHOT_URL)
    if resp.status_code != 200:
        return table
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        members = [m for m in zf.namelist() if m.endswith(".jsonl")]
        if not members:
            return table
        table.update(_parse(zf.read(members[0]), version))
    return table


def reset_cache() -> None:
    """Clear module cache (tests / re-runs)."""
    _cache.clear()


def resolve(
    client: httpx.Client, name: str, id_override: str | None = None
) -> tuple[dict[str, float], str] | None:
    """Blender resolver: ``({"blender_score": median}, url)`` or None."""
    hit = _load(client, DEFAULT_VERSION).get(normalize_gpu(name))
    if hit is None:
        return None
    return {"blender_score": hit}, SNAPSHOT_URL
