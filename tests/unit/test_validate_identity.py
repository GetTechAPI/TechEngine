"""Validator rules for identity fields, the device catalog and country-less brands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app import validate

SRC = ["https://example.com"]
BRAND = {"slug": "acme", "name": "Acme", "country": "US",
         "categories": ["smartphone-oem"], "source_urls": SRC}
CATALOG = {"slug": "acme-x", "name": "Acme X", "brand": "acme",
           "source_urls": SRC, "verified": False}


def _put(root: Path, rel: str, rec: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")


def _errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rel: str,
            rec: dict[str, Any]) -> list[str]:
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, "brand/us/acme.json", BRAND)
    _put(tmp_path, rel, rec)
    return validate.validate()


@pytest.mark.parametrize("value,ok", [
    ([], True), (["SM-S938N", "SM-S938B"], True), (["A", "A"], False),
    ([""], False), (["  "], False), ("SM-S938N", False), ([1], False),
])
def test_string_list(value: object, ok: bool) -> None:
    errors: list[str] = []
    validate._check_string_list("x.json", {"codenames": value}, "codenames", errors)
    assert (errors == []) is ok


@pytest.mark.parametrize("value,ok", [
    (None, True), ("day", True), ("month", True), ("year", True),
    ("year_estimated", True), ("decade", False), ("", False),
])
def test_release_date_precision(value: object, ok: bool) -> None:
    errors: list[str] = []
    validate._check_identity_fields("x.json", {"release_date_precision": value}, errors)
    assert (errors == []) is ok


def test_phone_battery_and_weight_optional(tmp_path, monkeypatch) -> None:
    _put(tmp_path, "soc/acme/c1.json", {
        "slug": "c1", "name": "C1", "manufacturer": "acme", "release_date": "2020-01-01",
        "process_nm": 7, "gpu_name": "G", "source_urls": SRC})
    phone = {"slug": "p", "name": "P", "brand": "acme", "soc": "c1",
             "release_date": "2020-01-01", "ram_gb": 4, "os": "Android", "source_urls": SRC}
    assert _errors(tmp_path, monkeypatch, "smartphone/acme/2020/p.json", phone) == []
    bad = {**phone, "battery_mah": 10, "codenames": ["x", "x"]}
    errs = _errors(tmp_path, monkeypatch, "smartphone/acme/2020/p.json", bad)
    assert any("battery_mah=10" in e for e in errs)
    assert any("codenames contains duplicates" in e for e in errs)


def test_catalog_valid_and_required(tmp_path, monkeypatch) -> None:
    rel = "device_catalog/acme/acme-x.json"
    assert _errors(tmp_path, monkeypatch, rel, CATALOG) == []
    full = {**CATALOG, "ram_gb": 3.5, "soc_raw": "MT6765", "gpu_raw": "PowerVR GE8320",
            "screen_resolution": "720x1600", "screen_density_dpi": 320,
            "android_sdk_min": 29, "android_sdk_max": 33,
            "release_year": 2020, "release_year_source": "model_code"}
    assert _errors(tmp_path, monkeypatch, rel, full) == []
    errs = _errors(tmp_path, monkeypatch, rel, {"slug": "acme-x", "name": "Acme X"})
    assert any("missing required fields ['brand', 'source_urls', 'verified']" in e for e in errs)


@pytest.mark.parametrize("patch,needle", [
    ({"form_factor": "phablet"}, "form_factor 'phablet'"),
    ({"device_type_guess": "tv-box"}, "device_type_guess 'tv-box'"),
    ({"android_sdk_min": "30"}, "android_sdk_min must be an integer"),
    ({"android_sdk_min": 0}, "android_sdk_min=0"),
    ({"promoted_to": "laptop/x"}, "promoted_to 'laptop/x'"),
    ({"promoted_to": "smartphone"}, "promoted_to 'smartphone'"),
    ({"marketing_names": ["a", "a"]}, "marketing_names contains duplicates"),
    ({"verified": "no"}, "verified must be a boolean"),
    ({"brand": "nobody"}, "brand 'nobody' not a known brand"),
    ({"base_model_slug": "Bad Slug"}, "invalid slug 'Bad Slug'"),
    ({"soc": "no-such-chip"}, "soc 'no-such-chip' not a known SoC"),
    ({"ram_gb": 512}, "ram_gb=512"),
    ({"ram_gb": True}, "ram_gb must be a number"),
    ({"soc_raw": ""}, "soc_raw must be a non-empty string"),
    ({"gpu_raw": 5}, "gpu_raw must be a non-empty string"),
    ({"screen_resolution": "1080*2400"}, "screen_resolution '1080*2400'"),
    ({"screen_density_dpi": 4000}, "screen_density_dpi=4000"),
    ({"android_sdk_min": 30, "android_sdk_max": 29}, "android_sdk_max=29 < android_sdk_min=30"),
    ({"release_year": 2021}, "release_year and release_year_source must be set together"),
    ({"release_year_source": "record"}, "must be set together"),
    ({"release_year": 2021, "release_year_source": "sdk"}, "release_year_source 'sdk'"),
    ({"release_year": 1900, "release_year_source": "record"}, "release_year=1900"),
])
def test_catalog_field_rules(tmp_path, monkeypatch, patch, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, "device_catalog/acme/acme-x.json", {**CATALOG, **patch})
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("rel,needle", [
    ("device_catalog/acme/2024/acme-x.json", "must live at 'device_catalog/<brand>/<slug>.json'"),
    ("device_catalog/other/acme-x.json", "lives in brand 'other'"),
    ("device_catalog/acme/wrong.json", "filename must match slug 'acme-x'"),
])
def test_catalog_path_rules(tmp_path, monkeypatch, rel, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, rel, CATALOG)
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("brand", [
    {k: v for k, v in BRAND.items() if k != "country"}, {**BRAND, "country": None},
], ids=["omitted", "null"])
def test_brand_country_optional_lives_under_unknown(tmp_path, monkeypatch, brand) -> None:
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, "brand/unknown/acme.json", brand)
    assert validate.validate() == []
    (tmp_path / "brand/unknown/acme.json").unlink()
    _put(tmp_path, "brand/us/acme.json", brand)
    assert any("expected 'unknown/'" in e for e in validate.validate())


def test_brand_country_still_checked_when_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, "brand/usa/acme.json", {**BRAND, "country": "USA"})
    assert any("ISO 3166" in e for e in validate.validate())
