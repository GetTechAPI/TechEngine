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


DEVICE = {"slug": "d", "name": "D", "brand": "acme", "source_urls": SRC, "verified": False}
DEVICE_SPECS = {"release_date": "2020-01-01", "ram_gb": 4, "os": "Android"}
MONITOR_SPECS = {"release_date": "2020-01-01", "size_inch": 27, "resolution": "2560x1440"}
DEVICE_CASES = [
    ("smartphone", "smartphone/acme/{year}/d.json", DEVICE_SPECS),
    ("smartphone-variant", "smartphone/acme/{year}/base/d.json", DEVICE_SPECS),
    ("tablet", "tablet/acme/{year}/base/d.json", DEVICE_SPECS),
    ("watch", "watch/acme/{year}/base/d.json", DEVICE_SPECS),
    ("pda", "pda/acme/{year}/base/d.json", DEVICE_SPECS),
    ("laptop", "laptop/acme/{year}/d.json", DEVICE_SPECS),
    ("monitor", "monitor/acme/{year}/d.json", MONITOR_SPECS),
]
device_cases = pytest.mark.parametrize(
    "rel,specs", [case[1:] for case in DEVICE_CASES], ids=[case[0] for case in DEVICE_CASES]
)


@device_cases
@pytest.mark.parametrize("null", [True, False], ids=["null", "omitted"])
def test_device_specs_optional(tmp_path, monkeypatch, rel, specs, null) -> None:
    device = {**DEVICE, **(dict.fromkeys(specs) if null else {})}
    assert _errors(tmp_path, monkeypatch, rel.format(year="unknown"), device) == []


@device_cases
def test_device_without_date_rejected_in_year_folder(tmp_path, monkeypatch, rel, specs) -> None:
    device = {**DEVICE, **specs, "release_date": None}
    errs = _errors(tmp_path, monkeypatch, rel.format(year="2020"), device)
    assert any("lives in year '2020' but release_date is null" in e for e in errs), errs


@device_cases
def test_device_with_date_rejected_in_unknown_folder(tmp_path, monkeypatch, rel, specs) -> None:
    device = {**DEVICE, **specs}
    assert _errors(tmp_path, monkeypatch, rel.format(year="2020"), device) == []
    (tmp_path / rel.format(year="2020")).unlink()
    errs = _errors(tmp_path, monkeypatch, rel.format(year="unknown"), device)
    assert any("lives in year 'unknown' but release_date starts with '2020'" in e
               for e in errs), errs


@device_cases
def test_device_specs_still_validated_when_set(tmp_path, monkeypatch, rel, specs) -> None:
    bad = {"size_inch": 500} if "size_inch" in specs else {"ram_gb": 4096}
    errs = _errors(tmp_path, monkeypatch, rel.format(year="2020"), {**DEVICE, **specs, **bad})
    assert any("out of range" in e for e in errs), errs
    errs = _errors(tmp_path, monkeypatch, rel.format(year="2020"),
                   {**DEVICE, **specs, "release_date": "2020"})
    assert any("must be ISO 8601" in e for e in errs), errs


CHIP = {"slug": "c", "name": "C", "manufacturer": "acme", "source_urls": SRC, "verified": False}
CPU_SPECS = {"segment": "desktop", "architecture": "Zen 4", "cores": 8, "threads": 16}
GPU_SPECS = {"architecture": "Ada", "memory_gb": 8, "memory_type": "GDDR6",
             "memory_bus_bit": 128, "base_clock_mhz": 1000, "boost_clock_mhz": 1200,
             "tdp_w": 150, "pcie_version": "PCIe 4.0 x16"}
SOC_SPECS = {"release_date": "2020-01-01", "process_nm": 7, "gpu_name": "G"}
DATED = {"release_date": "2020-01-01"}


@pytest.mark.parametrize("null", [True, False], ids=["null", "omitted"])
@pytest.mark.parametrize("rel,required,specs", [
    ("cpu/acme/2020/unknown/c.json", DATED, CPU_SPECS),
    ("gpu/acme/2020/consumer/c.json", DATED, GPU_SPECS),
    ("soc/acme/unknown/c.json", {}, SOC_SPECS),
    ("cpu/acme/unknown/unknown/c.json", {}, {**DATED, **CPU_SPECS}),
    ("gpu/acme/unknown/consumer/c.json", {}, {**DATED, **GPU_SPECS}),
], ids=["cpu", "gpu", "soc", "cpu-undated", "gpu-undated"])
def test_chip_specs_optional(tmp_path, monkeypatch, rel, required, specs, null) -> None:
    chip = {**CHIP, **required, **(dict.fromkeys(specs) if null else {})}
    assert _errors(tmp_path, monkeypatch, rel, chip) == []


@pytest.mark.parametrize("rel,specs", [
    ("cpu/acme/unknown/consumer/c.json", CPU_SPECS),
    ("cpu/acme/unknown/enterprise/c.json", {**CPU_SPECS, "segment": "server"}),
    ("gpu/acme/unknown/enterprise/c.json", GPU_SPECS),
])
def test_undated_cpu_and_gpu_keep_their_segment_folder(tmp_path, monkeypatch, rel, specs) -> None:
    assert _errors(tmp_path, monkeypatch, rel, {**CHIP, **specs, "release_date": None}) == []


@pytest.mark.parametrize("rel,chip,needle", [
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "segment": None},
     "segment is null, so it must live at"),
    ("cpu/acme/c.json", DATED, "segment is null, so it must live at"),
    ("cpu/acme/2020/unknown/c.json", {**DATED, **CPU_SPECS},
     "lives in 'unknown/' but segment='desktop'"),
    ("cpu/acme/2020/consumer/c.json", CPU_SPECS,
     "lives in year '2020' but release_date is null"),
    ("cpu/acme/unknown/consumer/c.json", {**DATED, **CPU_SPECS},
     "lives in year 'unknown' but release_date starts with '2020'"),
    ("cpu/acme/2019/consumer/c.json", {**DATED, **CPU_SPECS},
     "lives in year '2019' but release_date starts with '2020'"),
    ("cpu/acme/c.json", CPU_SPECS, "release_date is null, so it must live at"),
    ("gpu/acme/2020/consumer/c.json", {**GPU_SPECS, "release_date": None},
     "lives in year '2020' but release_date is null"),
    ("gpu/acme/unknown/consumer/c.json", {**DATED, **GPU_SPECS},
     "lives in year 'unknown' but release_date starts with '2020'"),
    ("gpu/acme/c.json", GPU_SPECS, "release_date is null, so it must live at"),
    ("soc/acme/2020/c.json", {**SOC_SPECS, "release_date": None},
     "lives in year '2020' but release_date is null"),
    ("soc/acme/c.json", {}, "release_date is null, so it must live at"),
    ("soc/acme/unknown/c.json", SOC_SPECS,
     "lives in year 'unknown' but release_date starts with '2020'"),
])
def test_chip_unknown_folder_rules(tmp_path, monkeypatch, rel, chip, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, rel, {**CHIP, **chip})
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("rel,chip,needle", [
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "cores": 0}, "out of range"),
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "segment": "phone"},
     "segment 'phone' not in"),
    ("gpu/acme/2020/consumer/c.json", {**DATED, **GPU_SPECS, "tdp_w": 9000}, "out of range"),
    ("gpu/acme/2020/consumer/c.json", {**DATED, **GPU_SPECS, "memory_bus_bit": 1.5},
     "must be an integer"),
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "release_date": "2020"},
     "must be ISO 8601"),
    ("gpu/acme/2020/consumer/c.json", {**DATED, **GPU_SPECS, "release_date": "2020-01"},
     "must be ISO 8601"),
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "tdp_w": -1}, "out of range"),
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "tdp_w": "65"}, "out of range"),
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "tdp_w": True},
     "must be a number"),
    ("cpu/acme/2020/consumer/c.json", {**DATED, **CPU_SPECS, "max_tdp_w": 9.5},
     "must be an integer"),
    ("gpu/acme/2020/consumer/c.json", {**DATED, **GPU_SPECS, "tdp_w": 0.5}, "out of range"),
    ("gpu/acme/2020/consumer/c.json", {**DATED, **GPU_SPECS, "tdp_w": True},
     "must be a number"),
    ("soc/acme/2020/c.json", {**SOC_SPECS, "process_nm": 500}, "out of range"),
    ("soc/acme/2020/c.json", {**SOC_SPECS, "release_date": "2020"}, "must be ISO 8601"),
])
def test_chip_specs_still_validated_when_set(tmp_path, monkeypatch, rel, chip, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, rel, {**CHIP, **chip})
    assert any(needle in e for e in errs), errs


CHIP_RELS = ["cpu/acme/2012/unknown/c.json", "gpu/acme/2012/consumer/c.json",
             "soc/acme/2012/c.json"]


@pytest.mark.parametrize("rel", CHIP_RELS, ids=["cpu", "gpu", "soc"])
@pytest.mark.parametrize("dated", [
    {"release_date": "2012-05-14"},
    {"release_date": "2012-05-14", "release_date_precision": None},
    {"release_date": "2012-05-14", "release_date_precision": "day"},
    {"release_date": "2012-05-01", "release_date_precision": "day"},
    {"release_date": "2012-05-01", "release_date_precision": "month"},
    {"release_date": "2012-01-01", "release_date_precision": "month"},
    {"release_date": "2012-01-01", "release_date_precision": "year"},
], ids=["absent", "null", "day", "day-on-the-first", "month", "month-january", "year"])
def test_chip_release_date_precision_accepted(tmp_path, monkeypatch, rel, dated) -> None:
    assert _errors(tmp_path, monkeypatch, rel, {**CHIP, **dated}) == []


@pytest.mark.parametrize("rel", CHIP_RELS, ids=["cpu", "gpu", "soc"])
@pytest.mark.parametrize("dated,needle", [
    ({"release_date": "2012-05-01", "release_date_precision": "decade"}, "not in"),
    ({"release_date": "2012-01-01", "release_date_precision": "year_estimated"}, "not in"),
    ({"release_date": "2012-05-01", "release_date_precision": ["month"]}, "not in"),
    ({"release_date": "2012-05-14", "release_date_precision": "month"}, "ending in '-01'"),
    ({"release_date": "2012-05-01", "release_date_precision": "year"}, "ending in '-01-01'"),
    ({"release_date": "2012-01-14", "release_date_precision": "year"}, "ending in '-01-01'"),
], ids=["bad-value", "device-only-value", "not-a-string", "month-with-day",
        "year-with-month", "year-with-day"])
def test_chip_release_date_precision_rejected(tmp_path, monkeypatch, rel, dated, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, rel, {**CHIP, **dated})
    assert any("release_date_precision" in e and needle in e for e in errs), errs


@pytest.mark.parametrize("rel", [
    "cpu/acme/unknown/unknown/c.json", "gpu/acme/unknown/consumer/c.json",
    "soc/acme/unknown/c.json",
], ids=["cpu", "gpu", "soc"])
@pytest.mark.parametrize("precision", ["day", "month", "year"])
def test_chip_release_date_precision_needs_a_date(tmp_path, monkeypatch, rel, precision) -> None:
    errs = _errors(tmp_path, monkeypatch, rel, {**CHIP, "release_date_precision": precision})
    assert any("needs a release_date" in e for e in errs), errs


@pytest.mark.parametrize("rel,chip", [
    ("cpu/acme/2020/consumer/c.json", {**CPU_SPECS, "tdp_w": 4.5}),
    ("cpu/acme/2020/consumer/c.json", {**CPU_SPECS, "tdp_w": 0.65}),
    ("cpu/acme/2020/consumer/c.json", {**CPU_SPECS, "tdp_w": 65}),
    ("gpu/acme/2020/consumer/c.json", {**GPU_SPECS, "tdp_w": 7.5}),
])
def test_fractional_tdp_accepted(tmp_path, monkeypatch, rel, chip) -> None:
    assert _errors(tmp_path, monkeypatch, rel, {**CHIP, **DATED, **chip}) == []


@pytest.mark.parametrize("rel,patch", [
    ("laptop/acme/2020/d.json", {"ram_gb": 0.000256}),
    ("laptop/acme/2020/d.json", {"ram_gb": 0.5}),
    ("laptop/acme/2020/d.json", {"ram_gb": 0.0001}),
    ("laptop/acme/2020/d.json", {"ram_gb": 256}),
    ("laptop/acme/2020/d.json", {"weight_g": 7800}),
    ("laptop/acme/2020/d.json", {"weight_g": 10000}),
    ("pda/acme/2020/base/d.json", {"ram_gb": 0.000128}),
    ("watch/acme/2020/base/d.json", {"ram_gb": 0.0001}),
])
def test_old_device_memory_and_weight_accepted(tmp_path, monkeypatch, rel, patch) -> None:
    assert _errors(tmp_path, monkeypatch, rel, {**DEVICE, **DEVICE_SPECS, **patch}) == []


@pytest.mark.parametrize("rel,patch,needle", [
    ("laptop/acme/2020/d.json", {"ram_gb": 0.00009}, "out of range [0.0001, 256]"),
    ("laptop/acme/2020/d.json", {"ram_gb": 512}, "out of range [0.0001, 256]"),
    ("laptop/acme/2020/d.json", {"ram_gb": True}, "must be a number"),
    ("laptop/acme/2020/d.json", {"weight_g": 10001}, "out of range [300, 10000]"),
    ("laptop/acme/2020/d.json", {"storage_gb": 0.5}, "must be an integer"),
    ("pda/acme/2020/base/d.json", {"ram_gb": 0.00009}, "out of range [0.0001, 64]"),
    ("watch/acme/2020/base/d.json", {"ram_gb": 0.00009}, "out of range [0.0001, 64]"),
])
def test_old_device_bounds_still_hold(tmp_path, monkeypatch, rel, patch, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, rel, {**DEVICE, **DEVICE_SPECS, **patch})
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("patch", [{"soc": None}, {}], ids=["null", "omitted"])
def test_phone_soc_optional(tmp_path, monkeypatch, patch) -> None:
    phone = {"slug": "p", "name": "P", "brand": "acme", "release_date": "2020-01-01",
             "ram_gb": 4, "os": "Android", "source_urls": SRC, **patch}
    assert _errors(tmp_path, monkeypatch, "smartphone/acme/2020/p.json", phone) == []


def test_phone_unknown_soc_still_rejected(tmp_path, monkeypatch) -> None:
    phone = {"slug": "p", "name": "P", "brand": "acme", "soc": "no-such-chip",
             "release_date": "2020-01-01", "ram_gb": 4, "os": "Android", "source_urls": SRC}
    errs = _errors(tmp_path, monkeypatch, "smartphone/acme/2020/p.json", phone)
    assert any("soc 'no-such-chip' not a known SoC" in e for e in errs), errs
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
    ({"android_sdk_min": "30"}, "android_sdk_min='30' must be an integer"),
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
