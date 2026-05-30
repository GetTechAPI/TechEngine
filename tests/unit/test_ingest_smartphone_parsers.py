"""Smartphone-specific normalize-helper tests."""

from __future__ import annotations

import pytest

from app.ingest.normalize import (
    guess_os,
    parse_battery_mah,
    parse_ram_gb,
    parse_weight_g,
)
from app.ingest.sources.wikipedia_smartphone import soc_text_to_slug


@pytest.mark.parametrize(
    "text,expected",
    [
        ("8 GB", 8),
        ("6/8/12 GB", 12),  # picks the largest config
        ("12GB", 12),
        ("512 MB", None),
        ("none", None),
    ],
)
def test_parse_ram_gb(text: str, expected: int | None) -> None:
    assert parse_ram_gb(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("5,000 mAh", 5000),
        ("4350 mAh", 4350),
        ("100 mAh", None),    # outside 500..12000 range
        ("99,999 mAh", None),
        ("", None),
    ],
)
def test_parse_battery_mah(text: str, expected: int | None) -> None:
    assert parse_battery_mah(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("232 g", 232),
        ("171.5 g", 171),
        ("49 g", None),      # outside 50..500 range
        ("3kg", None),       # not parsed (no plain "g")
        ("", None),
    ],
)
def test_parse_weight_g(text: str, expected: int | None) -> None:
    assert parse_weight_g(text) == expected


def test_guess_os_extracts_android_with_version() -> None:
    assert guess_os("Android 14, One UI 6.1", brand="samsung") == "Android 14"


def test_guess_os_explicit_ios() -> None:
    assert guess_os("iOS 17", brand="apple") == "iOS 17"


def test_guess_os_prefers_ipados_over_ios() -> None:
    assert guess_os("iPadOS 17", brand="apple") == "iPadOS 17"


def test_guess_os_falls_back_to_brand() -> None:
    assert guess_os("(unknown)", brand="samsung") == "Android"
    assert guess_os("(unknown)", brand="apple") == "iOS"


def test_guess_os_empty_returns_none() -> None:
    assert guess_os("", brand="samsung") is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Snapdragon 8 Gen 3", "snapdragon-8-gen-3"),
        ("Qualcomm Snapdragon 8 Gen 3 Mobile Platform", "snapdragon-8-gen-3"),
        ("Qualcomm Snapdragon 8 Gen 3 for Galaxy", "snapdragon-8-gen-3"),
        ("MediaTek Dimensity 9300", "dimensity-9300"),
        ("Apple A17 Pro", "a17-pro"),
        ("Samsung Exynos 2400", "exynos-2400"),
        ("Google Tensor G3", "tensor-g3"),
        ("", ""),
    ],
)
def test_soc_text_to_slug(text: str, expected: str) -> None:
    assert soc_text_to_slug(text) == expected
