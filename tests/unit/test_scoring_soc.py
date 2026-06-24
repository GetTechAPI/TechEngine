"""Unit tests for SoC scoring (§8)."""

from __future__ import annotations

from datetime import date

from app.models.soc import SoC
from app.services.scoring import score_soc


def _soc(**overrides: object) -> SoC:
    base: dict[str, object] = dict(
        slug="test-soc",
        name="Test SoC",
        manufacturer_id=1,
        release_date=date(2025, 1, 1),
        process_nm=3.0,
        gpu_name="Test GPU",
    )
    base.update(overrides)
    return SoC(**base)


def test_soc_blends_geekbench_and_antutu() -> None:
    score = score_soc(_soc(geekbench_single=3000, geekbench_multi=9000, antutu_score=2_500_000))
    assert score.algorithm_version == "2.0.0"
    for value in (score.overall, score.cpu.index, score.system.index):
        assert value is not None and 0.0 <= value <= 100.0
    assert score.cpu.source == "geekbench"
    assert score.system.source == "antutu_score"


def test_soc_cpu_axis_from_single_only() -> None:
    score = score_soc(_soc(geekbench_single=2000))
    assert score.cpu.index is not None  # blend tolerates a missing multi
    assert score.system.index is None  # no antutu


def test_soc_without_benchmark_is_null() -> None:
    score = score_soc(_soc())
    assert score.overall is None
    assert score.cpu.index is None and score.system.index is None
