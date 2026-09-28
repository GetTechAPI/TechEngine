"""Validate the shipped seed dataset and the validator itself (§9.3, §15.3)."""

from __future__ import annotations

import pytest

from app import validate


def test_shipped_seed_data_is_valid() -> None:
    assert validate.validate() == []


def test_slug_checker_rejects_bad_slugs() -> None:
    errors: list[str] = []
    validate._check_slug("x.json", "Not A Slug", errors)
    assert errors


def test_slug_checker_accepts_kebab_case() -> None:
    errors: list[str] = []
    validate._check_slug("x.json", "galaxy-s25-ultra", errors)
    assert errors == []


def test_range_checker_flags_out_of_range() -> None:
    errors: list[str] = []
    validate._check_range("x.json", "ram_gb", 999, 1, 64, errors)
    assert errors


@pytest.mark.parametrize("field,lo,hi", [("battery_mah", 50, 20000), ("weight_g", 10, 2000)])
def test_mobile_spec_ranges_allow_null_but_reject_invalid_values(
    field: str,
    lo: float,
    hi: float,
) -> None:
    errors: list[str] = []
    validate._check_range("x.json", field, None, lo, hi, errors)
    assert errors == []
    for value in (0, hi + 1, "unknown"):
        errors = []
        validate._check_range("x.json", field, value, lo, hi, errors)
        assert errors


def test_date_checker_requires_iso_format() -> None:
    errors: list[str] = []
    validate._check_date("x.json", "Jan 1 2025", errors)
    assert errors


def test_source_urls_checker_requires_non_empty_list() -> None:
    errors: list[str] = []
    validate._check_source_urls("x.json", {"source_urls": []}, errors)
    assert errors


def test_source_urls_checker_accepts_url_list() -> None:
    errors: list[str] = []
    validate._check_source_urls("x.json", {"source_urls": ["https://example.com"]}, errors)
    assert errors == []


def test_source_urls_checker_rejects_non_url_strings() -> None:
    errors: list[str] = []
    validate._check_source_urls("x.json", {"source_urls": ["example.com"]}, errors)
    assert errors


def test_brand_source_urls_are_required() -> None:
    errors: list[str] = []
    validate._check_required(
        "brand/example.json",
        {"slug": "example", "name": "Example", "country": "US", "categories": ["pc-oem"]},
        validate.BRAND_REQUIRED,
        errors,
    )
    assert any("source_urls" in error for error in errors)
