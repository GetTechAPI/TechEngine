"""Curated-slug loader unit tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.coverage.curated import curated_slugs, data_dir


def test_data_dir_respects_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    assert data_dir() == tmp_path


def test_curated_slugs_collects_from_subtree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    target = tmp_path / "cpu" / "intel" / "2024" / "consumer"
    target.mkdir(parents=True)
    (target / "test-a.json").write_text(json.dumps({"slug": "test-a"}), encoding="utf-8")
    (target / "test-b.json").write_text(json.dumps({"slug": "test-b"}), encoding="utf-8")
    result = curated_slugs("cpu", "intel")
    assert result == {"test-a", "test-b"}


def test_curated_slugs_missing_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    assert curated_slugs("does-not-exist") == set()


def test_curated_slugs_skips_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    target = tmp_path / "cpu" / "intel"
    target.mkdir(parents=True)
    (target / "broken.json").write_text("{not json", encoding="utf-8")
    (target / "good.json").write_text(json.dumps({"slug": "good"}), encoding="utf-8")
    assert curated_slugs("cpu", "intel") == {"good"}


def test_curated_slugs_ignores_records_without_slug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    target = tmp_path / "cpu" / "intel"
    target.mkdir(parents=True)
    (target / "no-slug.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
    (target / "ok.json").write_text(json.dumps({"slug": "ok"}), encoding="utf-8")
    assert curated_slugs("cpu", "intel") == {"ok"}
