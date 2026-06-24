"""Unit tests for smartphone scoring (§8, §15.1)."""

from __future__ import annotations

from datetime import date

from app.models.smartphone import Smartphone
from app.models.soc import SoC
from app.services.scoring import ALGORITHM_VERSION, DatasetStats, score_phone


def _soc(**overrides: object) -> SoC:
    base: dict[str, object] = dict(
        slug="test-soc",
        name="Test SoC",
        manufacturer_id=1,
        release_date=date(2024, 1, 1),
        process_nm=3.0,
        gpu_name="Test GPU",
        geekbench_single=3000,
        geekbench_multi=9000,
        antutu_score=2_500_000,
    )
    base.update(overrides)
    return SoC(**base)


def _phone(**overrides: object) -> Smartphone:
    base: dict[str, object] = dict(
        slug="test-phone",
        name="Test Phone",
        brand_id=1,
        soc_id=1,
        release_date=date(2024, 1, 1),
        msrp_usd=999,
        ram_gb=12,
        battery_mah=5000,
        charging_wired_w=45,
        charging_wireless_w=15,
        weight_g=180.0,
        os="Android",
        display={"refresh_hz": 120, "brightness_nits": 2600, "ppi": 460},
        cameras=[
            {"type": "main", "mp": 50, "ois": True},
            {"type": "ultrawide", "mp": 12},
            {"type": "selfie", "mp": 12},
        ],
    )
    base.update(overrides)
    return Smartphone(**base)


def test_scores_within_0_100_and_version() -> None:
    score = score_phone(_phone(), _soc())
    assert score.algorithm_version == ALGORITHM_VERSION == "2.0.0"
    for value in (
        score.overall,
        score.performance,
        score.camera,
        score.battery,
        score.display,
        score.value,
    ):
        assert value is not None and 0.0 <= value <= 100.0


def test_performance_equals_perf_index() -> None:
    score = score_phone(_phone(), _soc())
    assert score.performance == score.perf.index
    assert score.perf.era == "2024-2026"


def test_no_soc_benchmark_yields_null_performance() -> None:
    # benchmark-only: every SoC benchmark missing -> null perf, never spec-estimated
    score = score_phone(
        _phone(), _soc(geekbench_single=None, geekbench_multi=None, antutu_score=None)
    )
    assert score.performance is None
    assert score.perf.index is None


def test_antutu_alone_still_scores_performance() -> None:
    score = score_phone(_phone(), _soc(geekbench_single=None, geekbench_multi=None))
    assert score.performance is not None  # AnTuTu is a benchmark


def test_missing_camera_yields_null() -> None:
    assert score_phone(_phone(cameras=[]), _soc()).camera is None


def test_value_requires_msrp() -> None:
    assert score_phone(_phone(msrp_usd=None), _soc()).value is None


def test_relative_fields_filled_only_with_stats() -> None:
    soc = _soc()
    plain = score_phone(_phone(), soc)
    assert plain.perf.percentile is None and plain.perf.tier is None
    stats = DatasetStats({("phone", "perf", "2024-2026"): [0.0, 50.0, 100.0]})
    ranked = score_phone(_phone(), soc, stats=stats)
    assert ranked.perf.percentile is not None
    assert ranked.perf.tier is not None
