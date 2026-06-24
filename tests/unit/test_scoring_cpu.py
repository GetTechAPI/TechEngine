"""Unit tests for CPU scoring (§8)."""

from __future__ import annotations

from datetime import date

from app.models.cpu import CPU
from app.services.scoring import score_cpu


def _cpu(**overrides: object) -> CPU:
    base: dict[str, object] = dict(
        slug="test-cpu",
        name="Test CPU",
        manufacturer_id=1,
        release_date=date(2024, 1, 1),
        segment="desktop",
        architecture="Test",
        cores=8,
        threads=16,
    )
    base.update(overrides)
    return CPU(**base)


def test_modern_cpu_scores_within_bounds_with_version() -> None:
    score = score_cpu(_cpu(cinebench_r23_single=2000, cinebench_r23_multi=35000))
    assert score.algorithm_version == "2.0.0"
    for value in (score.overall, score.single.index, score.multi.index):
        assert value is not None and 0.0 <= value <= 100.0
    assert score.single.source == "cinebench_r23_single"
    assert score.multi.source == "cinebench_r23_multi"
    assert score.multi.era == "2024-2026"


def test_chain_falls_back_to_legacy_benchmark() -> None:
    # only an old Cinebench R10 multi present -> still scored, via the fallback chain
    score = score_cpu(_cpu(release_date=date(2008, 6, 1), cinebench_r10_multi=8000))
    assert score.multi.index is not None
    assert score.multi.source == "cinebench_r10_multi"
    assert score.single.index is None  # no single-thread benchmark


def test_no_benchmark_yields_null_overall_not_zero() -> None:
    score = score_cpu(_cpu())  # no benchmark fields at all
    assert score.overall is None  # §8.2 null, never 0
    assert score.single.index is None and score.multi.index is None
    # era is still attached even with no benchmark
    assert score.multi.era == "2024-2026"
