"""Slug normalization unit tests."""

from __future__ import annotations

from app.coverage.normalize import slugify


def test_strips_intel_prefix() -> None:
    assert slugify("Intel Core i9-14900K", manufacturer="intel") == "core-i9-14900k"


def test_strips_amd_prefix() -> None:
    assert slugify("AMD Ryzen 9 9950X3D", manufacturer="amd") == "ryzen-9-9950x3d"


def test_strips_advanced_micro_devices_prefix() -> None:
    assert (
        slugify("Advanced Micro Devices Ryzen 5 7600X", manufacturer="amd")
        == "ryzen-5-7600x"
    )


def test_strips_nvidia_prefix() -> None:
    assert (
        slugify("NVIDIA GeForce RTX 5090", manufacturer="nvidia")
        == "geforce-rtx-5090"
    )


def test_handles_diacritics() -> None:
    assert slugify("Café Au Lait") == "cafe-au-lait"


def test_collapses_internal_separators() -> None:
    assert slugify("Core i7    9700KF") == "core-i7-9700kf"


def test_strips_trailing_punctuation() -> None:
    assert slugify("Core i9-14900K!!") == "core-i9-14900k"


def test_empty_input() -> None:
    assert slugify("") == ""


def test_unknown_manufacturer_is_treated_as_literal_prefix() -> None:
    # Falls back to using the manufacturer slug itself as the prefix to strip.
    assert slugify("acme widget-9000", manufacturer="acme") == "widget-9000"


def test_does_not_strip_when_no_manufacturer_given() -> None:
    assert slugify("Intel Core i9-14900K") == "intel-core-i9-14900k"
