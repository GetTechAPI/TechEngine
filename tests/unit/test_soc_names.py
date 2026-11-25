"""SoC name rules: placeholders are rejected and one chip has one record."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app import validate
from app.soc_names import is_placeholder, soc_key

REPO = Path(__file__).resolve().parents[2]
SRC = ["https://example.com"]
BRAND = {"slug": "acme", "name": "Acme", "country": "US",
         "categories": ["soc-designer"], "source_urls": SRC}


def _soc(slug: str, name: str) -> dict[str, Any]:
    return {"slug": slug, "name": name, "manufacturer": "acme", "release_date": "2017-01-01",
            "process_nm": 14.0, "gpu_name": "Adreno 512", "verified": False, "source_urls": SRC}


def _put(root: Path, rel: str, rec: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")


@pytest.mark.parametrize("a,b", [
    ("Qualcomm SDM660 Snapdragon 660", "Snapdragon 660"),
    ("Qualcomm Snapdragon 660 SDM660", "Snapdragon 660"),
    ("MediaTek Helio G99 MT6789", "Helio G99"),
    ("Samsung Exynos 7 Octa 7870", "Exynos 7870"),
    ("HiSilicon Honor KIRIN710", "Kirin 710"),
    ("Qualcomm Snapdragon 8 Gen3", "Snapdragon 8 Gen 3"),
    ("Mediatek MT6735M (28 nm)", "MediaTek MT6735M"),
    ("Kirin 990", "Kirin 990 4G"),
])
def test_spellings_of_one_chip_share_a_key(a: str, b: str) -> None:
    assert soc_key(a) is not None
    assert soc_key(a) == soc_key(b)


@pytest.mark.parametrize("a,b", [
    ("Snapdragon 855", "Snapdragon 855+"),
    ("Snapdragon 855", "Snapdragon 855 Plus"),
    ("Snapdragon 778G", "Snapdragon 778G+"),
    ("Snapdragon 8 Gen 1", "Snapdragon 8+ Gen 1"),
    ("Kirin 990 4G", "Kirin 990 5G"),
    ("Kirin 710", "Kirin 710F"),
    ("Mediatek MT6735", "Mediatek MT6735M"),
    ("Helio G99", "Helio G99 Ultra"),
])
def test_different_chips_keep_different_keys(a: str, b: str) -> None:
    assert soc_key(a) is not None and soc_key(b) is not None
    assert soc_key(a) != soc_key(b)


def test_plus_is_spelled_one_way() -> None:
    assert soc_key("Snapdragon 855 Plus") == soc_key("Snapdragon 855+")


@pytest.mark.parametrize("name", [
    "Nokia mobile platform 2010", "1.0 GHz", "Dual-core 1.2 GHz", "Quad-core 1.2 GHz Cortex-A7",
    "800 MHz ARM 11", "0", "N/A", "Qualcomm", "Qualcomm Snapdragon", "Mediatek Helio",
    "Qualcomm Adreno 305", "Arm Mali T720",
])
def test_placeholders(name: str) -> None:
    assert is_placeholder(name)
    assert soc_key(name) is None


@pytest.mark.parametrize("name", [
    "Hummingbird", "Snapdragon 8 Elite", "Snapdragon 660", "Mediatek MT6582", "Apple A15 Bionic",
    "Nokia Rapido", "Exynos 2200",
])
def test_chips_are_not_placeholders(name: str) -> None:
    assert not is_placeholder(name)


def test_two_chips_in_one_name_have_no_key() -> None:
    assert soc_key("Mediatek MT6592MQualcomm MSM8916 Snapdragon 410") is None


def test_validator_rejects_a_placeholder_soc(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, "brand/us/acme.json", BRAND)
    _put(tmp_path, "soc/acme/2017/snapdragon-660.json", _soc("snapdragon-660", "Snapdragon 660"))
    assert validate.validate() == []
    _put(tmp_path, "soc/acme/2010/nokia-mobile-platform-2010.json",
         _soc("nokia-mobile-platform-2010", "Nokia mobile platform 2010"))
    errs = validate.validate()
    assert len(errs) == 1, errs
    assert "name 'Nokia mobile platform 2010' is a placeholder, not a chip" in errs[0]


def _integrity(root: Path, *args: str) -> list[str]:
    report = root / "hard.json"
    subprocess.run(
        [sys.executable, "integrity_check.py", str(root), "--hard-report", str(report), *args],
        cwd=REPO, capture_output=True, text=True, encoding="utf-8", check=True,
    )
    return json.loads(report.read_text(encoding="utf-8"))


def test_integrity_flags_one_chip_stored_twice(tmp_path) -> None:
    _put(tmp_path, "soc/acme/2017/snapdragon-660.json", _soc("snapdragon-660", "Snapdragon 660"))
    _put(tmp_path, "soc/acme/2019/snapdragon-855.json", _soc("snapdragon-855", "Snapdragon 855"))
    _put(tmp_path, "soc/acme/2019/snapdragon-855-plus.json",
         _soc("snapdragon-855-plus", "Snapdragon 855+"))
    assert not any("DUP chip" in item for item in _integrity(tmp_path))

    _put(tmp_path, "soc/acme/2017/qualcomm-sdm660-snapdragon-660.json",
         _soc("qualcomm-sdm660-snapdragon-660", "Qualcomm SDM660 Snapdragon 660"))
    found = [item for item in _integrity(tmp_path) if "DUP chip" in item]
    assert len(found) == 1, found
    assert "'snapdragon 660'" in found[0]
    assert "qualcomm-sdm660-snapdragon-660" in found[0] and "'snapdragon-660'" in found[0]


def test_scoped_integrity_compares_a_changed_soc_with_the_catalog(tmp_path) -> None:
    _put(tmp_path, "soc/acme/2017/snapdragon-660.json", _soc("snapdragon-660", "Snapdragon 660"))
    _put(tmp_path, "soc/acme/2017/qualcomm-sdm660-snapdragon-660.json",
         _soc("qualcomm-sdm660-snapdragon-660", "Qualcomm SDM660 Snapdragon 660"))
    only = tmp_path / "only.txt"
    only.write_text("soc/acme/2017/qualcomm-sdm660-snapdragon-660.json\n", encoding="utf-8")
    found = [item for item in _integrity(tmp_path, "--only", str(only)) if "DUP chip" in item]
    assert len(found) == 1, found
    assert "'snapdragon-660'" in found[0]
