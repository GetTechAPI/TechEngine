"""Unit tests for scoring primitives (§8)."""

from __future__ import annotations

from datetime import date

from app.services.scoring.common import (
    Hybrid,
    ReferenceScale,
    axis,
    capability,
    combine,
    era_band,
    tier_label,
    with_relative,
)
from app.services.scoring.config import load_config

BANDS = [(0, 2005, "pre-2006"), (2006, 2023, "2006-2023"), (2024, 2999, "2024-2026")]
TIERS = [(95.0, "S"), (80.0, "A"), (60.0, "B"), (40.0, "C"), (20.0, "D"), (5.0, "E"), (0.0, "F")]


def test_capability_linear_clamps() -> None:
    ref = ReferenceScale(0, 100)
    assert capability(-50, ref) == 0.0
    assert capability(150, ref) == 100.0
    assert capability(50, ref) == 50.0
    assert capability(None, ref) is None


def test_capability_invert() -> None:
    ref = ReferenceScale(2.0, 7.0, invert=True)
    assert capability(2.0, ref) == 100.0  # smaller is better
    assert capability(7.0, ref) == 0.0


def test_capability_log_is_monotonic_across_eras() -> None:
    ref = ReferenceScale(100, 100000, log=True)
    old = capability(150, ref)
    new = capability(90000, ref)
    assert old is not None and new is not None
    assert old < new  # a tiny old benchmark scores far below a huge modern one


def test_combine_renormalizes_over_present() -> None:
    assert combine([(80.0, 0.5), (None, 0.5)]) == 80.0  # missing part drops out
    assert combine([(None, 1.0)]) is None
    assert combine([(100.0, 0.25), (0.0, 0.75)]) == 25.0


def test_era_band_boundaries() -> None:
    assert era_band(date(2005, 12, 31), BANDS) == "pre-2006"
    assert era_band(date(2006, 1, 1), BANDS) == "2006-2023"
    assert era_band(date(2026, 6, 1), BANDS) == "2024-2026"
    assert era_band(None, BANDS) is None


def test_tier_label_thresholds() -> None:
    assert tier_label(96, TIERS) == "S"
    assert tier_label(80, TIERS) == "A"
    assert tier_label(5, TIERS) == "E"
    assert tier_label(0, TIERS) == "F"
    assert tier_label(None, TIERS) is None


def test_axis_picks_first_present_and_records_source() -> None:
    scales = {"a": ReferenceScale(0, 100), "b": ReferenceScale(0, 100)}
    chain = ["a", "b"]
    got = axis({"a": None, "b": 50.0}, chain, scales, era="2024-2026")
    assert got.index == 50.0 and got.source == "b" and got.era == "2024-2026"
    # benchmark-only: nothing present -> index-less, era retained
    none = axis({"a": None, "b": None}, chain, scales, era="2024-2026")
    assert none.index is None and none.source is None and none.era == "2024-2026"


class _Stats:
    def percentile(self, category: str, dimension: str, era: str, index: float) -> float | None:
        return 72.0


def test_with_relative_fills_percentile_and_tier() -> None:
    base = Hybrid(index=88.0, era="2024-2026", source="x")
    filled = with_relative(base, "cpu", "multi", _Stats(), TIERS)
    assert filled.percentile == 72.0 and filled.tier == "B"
    # no stats -> unchanged
    assert with_relative(base, "cpu", "multi", None, TIERS).percentile is None


def test_config_loads_and_version_matches_settings() -> None:
    cfg = load_config()
    assert cfg.algorithm_version == "2.0.0"
    assert "cinebench_r23_multi" in cfg.reference_scales["cpu"]
    assert cfg.chains["cpu"]["multi"][0] == "cinebench_r23_multi"
