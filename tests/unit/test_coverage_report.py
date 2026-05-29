"""Report builder unit tests with synthetic ``CoveragePoint`` s."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.coverage.report import build_report
from app.coverage.sources.base import CoveragePoint


def _point(category: str, manufacturer: str, slug: str, name: str | None = None) -> CoveragePoint:
    return CoveragePoint(
        category=category,
        manufacturer=manufacturer,
        name=name or slug,
        slug=slug,
        source="test",
        url=f"https://example.test/{slug}",
    )


def test_report_lists_missing_when_curated_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    points = [_point("cpu", "intel", "core-i9-14900k", "Intel Core i9-14900K")]
    report = build_report(points)
    assert "## cpu / intel — 1 missing" in report
    assert "`core-i9-14900k`" in report
    assert "https://example.test/core-i9-14900k" in report
    assert "**Total missing:** 1" in report


def test_report_subtracts_curated_slugs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    curated_dir = tmp_path / "cpu" / "intel"
    curated_dir.mkdir(parents=True)
    (curated_dir / "have.json").write_text(
        json.dumps({"slug": "core-i9-14900k"}), encoding="utf-8"
    )
    points = [
        _point("cpu", "intel", "core-i9-14900k"),
        _point("cpu", "intel", "core-i7-14700k"),
    ]
    report = build_report(points)
    assert "## cpu / intel — 1 missing" in report
    assert "`core-i7-14700k`" in report
    assert "core-i9-14900k" not in report.split("## cpu")[1]


def test_report_top_n_truncates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    points = [_point("cpu", "intel", f"sku-{i:03d}") for i in range(50)]
    report = build_report(points, top_n=10)
    assert "## cpu / intel — 50 missing" in report
    assert "and 40 more" in report


def test_report_groups_by_category_and_manufacturer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    points = [
        _point("cpu", "intel", "x"),
        _point("cpu", "amd", "y"),
        _point("gpu", "nvidia", "z"),
    ]
    report = build_report(points)
    assert "## cpu / amd" in report
    assert "## cpu / intel" in report
    assert "## gpu / nvidia" in report
    assert "**Total missing:** 3" in report
