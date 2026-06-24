"""Unit tests for GPU scoring (§8)."""

from __future__ import annotations

from datetime import date

from app.models.gpu import DiscreteGPU
from app.services.scoring import score_gpu


def _gpu(**overrides: object) -> DiscreteGPU:
    base: dict[str, object] = dict(
        slug="test-gpu",
        name="Test GPU",
        manufacturer_id=1,
        architecture="Test",
        release_date=date(2025, 1, 1),
        memory_gb=16.0,
        memory_type="GDDR7",
        memory_bus_bit=256,
        base_clock_mhz=2000,
        boost_clock_mhz=2500,
        tdp_w=300,
        pcie_version="PCIe 5.0 x16",
    )
    base.update(overrides)
    return DiscreteGPU(**base)


def test_gpu_scores_within_bounds_with_source() -> None:
    score = score_gpu(_gpu(timespy_score=30000))
    assert score.algorithm_version == "2.0.0"
    assert score.overall is not None and 0.0 <= score.overall <= 100.0
    assert score.graphics.index == score.overall
    assert score.graphics.source == "timespy_score"
    assert score.graphics.era == "2024-2026"


def test_gpu_chain_falls_back_to_tflops() -> None:
    score = score_gpu(_gpu(fp32_tflops=32.0))  # no timespy/g3d
    assert score.graphics.index is not None
    assert score.graphics.source == "fp32_tflops"


def test_gpu_without_benchmark_is_null() -> None:
    score = score_gpu(_gpu())
    assert score.overall is None
    assert score.graphics.index is None
