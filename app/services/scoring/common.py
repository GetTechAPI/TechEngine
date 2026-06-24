"""Scoring primitives shared by every category (§8, ADR-013).

The hybrid model exposes, per compute axis, both an **absolute** capability index
(0-100, calibrated against pinned reference scales — log where benchmarks span
orders of magnitude across eras) and a **within-generation** relative view
(percentile + letter tier among same-era peers). Inputs that are missing yield
``None`` (never 0).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from math import log
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ReferenceScale:
    """Absolute [lo, hi] in raw benchmark units → 0-100 capability."""

    lo: float
    hi: float
    log: bool = False
    invert: bool = False


@dataclass(frozen=True, slots=True)
class Hybrid:
    """One compute axis: absolute index + within-era relative standing + provenance."""

    index: float | None = None
    percentile: float | None = None
    tier: str | None = None
    era: str | None = None
    source: str | None = None  # benchmark NAME the index came from (never the raw value)


def capability(value: float | None, ref: ReferenceScale) -> float | None:
    """Map a raw benchmark ``value`` to 0-100 against ``ref`` (log/linear, clamped)."""
    if value is None:
        return None
    x = min(max(float(value), ref.lo), ref.hi)
    if ref.log:
        if ref.lo <= 0 or ref.hi <= ref.lo:
            return None
        t = (log(x) - log(ref.lo)) / (log(ref.hi) - log(ref.lo))
    else:
        span = ref.hi - ref.lo
        if span <= 0:
            return None
        t = (x - ref.lo) / span
    if ref.invert:
        t = 1.0 - t
    return round(100.0 * max(0.0, min(1.0, t)), 1)


def combine(parts: list[tuple[float | None, float]]) -> float | None:
    """Weighted mean over the present (non-None) parts, renormalizing weights.

    Returns ``None`` when no part is present.
    """
    present = [(v, w) for v, w in parts if v is not None and w > 0]
    if not present:
        return None
    total_w = sum(w for _v, w in present)
    if total_w <= 0:
        return None
    return round(sum(v * w for v, w in present) / total_w, 1)


def era_band(release: date | None, bands: list[tuple[int, int, str]]) -> str | None:
    """Map a release year to its configured generation band label."""
    if release is None:
        return None
    year = release.year
    for lo, hi, label in bands:
        if lo <= year <= hi:
            return label
    return None


def tier_label(percentile: float | None, tiers: list[tuple[float, str]]) -> str | None:
    """Map a percentile (0-100) to a letter tier via descending thresholds."""
    if percentile is None:
        return None
    for threshold, label in tiers:
        if percentile >= threshold:
            return label
    return None


def axis(
    raw_values: dict[str, float | None],
    chain: list[str],
    scales: dict[str, ReferenceScale],
    era: str | None,
) -> Hybrid:
    """Build the absolute side of a Hybrid from the first present benchmark in ``chain``.

    Benchmark-only: returns an index-less Hybrid (still carrying ``era``) when no
    benchmark in the chain has a value.
    """
    for name in chain:
        value = raw_values.get(name)
        if value is None:
            continue
        scale = scales.get(name)
        if scale is None:
            continue
        index = capability(value, scale)
        if index is None:
            continue
        return Hybrid(index=index, era=era, source=name)
    return Hybrid(era=era)


class StatsLike(Protocol):
    """Structural type for the dataset cohort lookup (see ``stats.DatasetStats``)."""

    def percentile(
        self, category: str, dimension: str, era: str, index: float
    ) -> float | None: ...


def with_relative(
    hybrid: Hybrid,
    category: str,
    dimension: str,
    stats: StatsLike | None,
    tiers: list[tuple[float, str]],
) -> Hybrid:
    """Fill ``percentile``/``tier`` from the same-era cohort (no-op without stats)."""
    if hybrid.index is None or hybrid.era is None or stats is None:
        return hybrid
    pct = stats.percentile(category, dimension, hybrid.era, hybrid.index)
    return replace(hybrid, percentile=pct, tier=tier_label(pct, tiers))
