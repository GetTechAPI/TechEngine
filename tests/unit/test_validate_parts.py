"""Validator and integrity rules for sockets and chipsets."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from app import validate

REPO = Path(__file__).resolve().parents[2]
SRC = ["https://example.com"]
BRAND = {"slug": "acme", "name": "Acme", "country": "US",
         "categories": ["chipset-maker"], "source_urls": SRC}
SOCKET = {"slug": "lga1156", "name": "LGA 1156", "manufacturer": "acme", "aliases": ["Socket H"],
          "release_date": "2009", "package_type": "LGA", "pin_count": 1156,
          "verified": False, "source_urls": SRC}
CHIPSET = {"slug": "acme-p55", "name": "P55", "manufacturer": "acme", "aliases": [],
           "series": "5 Series", "release_date": "2009-09", "sockets": ["lga1156"],
           "segment": "desktop", "cpu_overclocking": True, "memory_types": ["DDR3"],
           "max_memory_gb": 16, "pcie_version": "2.0", "usb_ports": {"USB 2.0": 14},
           "sata_ports": 6, "tdp_w": 4.7, "process_nm": 65, "verified": False, "source_urls": SRC}
SOCKET_REL = "socket/lga1156.json"
CHIPSET_REL = "chipset/acme/acme-p55.json"


def _put(root: Path, rel: str, rec: dict[str, Any]) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec), encoding="utf-8")


def _errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
            records: dict[str, dict[str, Any]]) -> list[str]:
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, "brand/us/acme.json", BRAND)
    for rel, rec in records.items():
        _put(tmp_path, rel, rec)
    return validate.validate()


def test_valid_socket_and_chipset(tmp_path, monkeypatch) -> None:
    assert _errors(tmp_path, monkeypatch, {SOCKET_REL: SOCKET, CHIPSET_REL: CHIPSET}) == []


def test_missing_folders_are_empty_collections(tmp_path, monkeypatch) -> None:
    assert _errors(tmp_path, monkeypatch, {}) == []
    assert not (tmp_path / "socket").exists()
    assert not (tmp_path / "chipset").exists()


def test_specs_optional(tmp_path, monkeypatch) -> None:
    socket = {"slug": "socket-7", "name": "Socket 7", "manufacturer": None,
              "verified": False, "source_urls": SRC}
    bare = {"slug": "acme-x", "name": "X", "manufacturer": "acme",
            "verified": False, "source_urls": SRC}
    nulled = {**dict.fromkeys(CHIPSET), **bare, "slug": "acme-y", "name": "Y",
              "aliases": [], "sockets": []}
    assert _errors(tmp_path, monkeypatch, {
        "socket/socket-7.json": socket,
        "socket/socket-8.json": {**dict.fromkeys(SOCKET), **socket, "slug": "socket-8",
                                 "name": "Socket 8", "aliases": []},
        "chipset/acme/acme-x.json": bare,
        "chipset/acme/acme-y.json": nulled,
    }) == []


@pytest.mark.parametrize("value,ok", [
    ("2009", True), ("2009-09", True), ("2009-09-08", True), (None, True),
    ("2009-9", False), ("09/2009", False), ("1990s", False), (2009, False), ("", False),
])
@pytest.mark.parametrize("rel,base", [(SOCKET_REL, SOCKET), (CHIPSET_REL, CHIPSET)],
                         ids=["socket", "chipset"])
def test_relaxed_release_date(tmp_path, monkeypatch, rel, base, value, ok) -> None:
    records = {SOCKET_REL: SOCKET, rel: {**base, "release_date": value}}
    errs = _errors(tmp_path, monkeypatch, records)
    assert (errs == []) is ok, errs
    if not ok:
        assert "must be YYYY, YYYY-MM or YYYY-MM-DD" in errs[0]


def test_other_categories_keep_the_strict_date(tmp_path, monkeypatch) -> None:
    soc = {"slug": "c1", "name": "C1", "manufacturer": "acme", "release_date": "2020",
           "source_urls": SRC}
    errs = _errors(tmp_path, monkeypatch, {"soc/acme/2020/c1.json": soc})
    assert any("must be ISO 8601" in e for e in errs), errs


@pytest.mark.parametrize("patch,needle", [
    ({"manufacturer": "nobody"}, "manufacturer 'nobody' not a known brand"),
    ({"aliases": "Socket H"}, "aliases must be a list of non-empty strings"),
    ({"package_type": ""}, "package_type must be a non-empty string or null"),
    ({"pin_count": 0}, "pin_count=0 out of range [1, 10000]"),
    ({"pin_count": 10001}, "pin_count=10001 out of range [1, 10000]"),
    ({"pin_count": "1156"}, "pin_count='1156' must be an integer"),
    ({"verified": "no"}, "verified must be a boolean"),
    ({"source_urls": []}, "source_urls must be a non-empty list"),
    ({"slug": "LGA_1156"}, "invalid slug"),
])
def test_socket_field_rules(tmp_path, monkeypatch, patch, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, {SOCKET_REL: {**SOCKET, **patch}})
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("field", ["slug", "name", "verified", "source_urls"])
def test_socket_required(tmp_path, monkeypatch, field) -> None:
    socket = {k: v for k, v in SOCKET.items() if k != field}
    errs = _errors(tmp_path, monkeypatch, {SOCKET_REL: socket})
    assert any(f"missing required fields ['{field}']" in e for e in errs), errs


@pytest.mark.parametrize("rel,needle", [
    ("socket/acme/lga1156.json", "sockets must live at 'socket/<slug>.json'"),
    ("socket/other.json", "filename must match slug 'lga1156'"),
])
def test_socket_path_rules(tmp_path, monkeypatch, rel, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, {rel: SOCKET})
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("patch,needle", [
    ({"manufacturer": None}, "manufacturer 'None' not a known brand"),
    ({"sockets": ["am5"]}, "socket 'am5' not a known socket"),
    ({"sockets": "lga1156"}, "sockets must be a list of socket slugs"),
    ({"sockets": ["lga1156", "lga1156"]}, "sockets contains duplicates"),
    ({"segment": "laptop"}, "segment 'laptop' not in"),
    ({"cpu_overclocking": "yes"}, "cpu_overclocking must be a boolean or null"),
    ({"memory_types": "DDR3"}, "memory_types must be a list of non-empty strings"),
    ({"max_memory_gb": "16"}, "max_memory_gb=16 out of range"),
    ({"max_memory_gb": True}, "max_memory_gb=True must be a number"),
    ({"pcie_version": 2.0}, "pcie_version must be a non-empty string or null"),
    ({"usb_ports": {"USB 2.0": "14"}}, "usb_ports must map a port type to an integer count"),
    ({"usb_ports": ["USB 2.0"]}, "usb_ports must map a port type to an integer count"),
    ({"sata_ports": 6.5}, "sata_ports=6.5 must be an integer"),
    ({"tdp_w": 0}, "tdp_w=0 out of range"),
    ({"process_nm": 0.5}, "process_nm=0.5 out of range"),
    ({"series": " "}, "series must be a non-empty string or null"),
])
def test_chipset_field_rules(tmp_path, monkeypatch, patch, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, {SOCKET_REL: SOCKET, CHIPSET_REL: {**CHIPSET, **patch}})
    assert any(needle in e for e in errs), errs


@pytest.mark.parametrize("rel,needle", [
    ("chipset/acme-p55.json", "chipsets must live at 'chipset/<manufacturer>/<slug>.json'"),
    ("chipset/other/acme-p55.json", "lives in manufacturer 'other' but manufacturer='acme'"),
    ("chipset/acme/p55.json", "filename must match slug 'acme-p55'"),
])
def test_chipset_path_rules(tmp_path, monkeypatch, rel, needle) -> None:
    errs = _errors(tmp_path, monkeypatch, {SOCKET_REL: SOCKET, rel: CHIPSET})
    assert any(needle in e for e in errs), errs


def test_scoped_run_still_resolves_sockets(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(validate, "DATA_DIR", tmp_path)
    _put(tmp_path, "brand/us/acme.json", BRAND)
    _put(tmp_path, SOCKET_REL, SOCKET)
    _put(tmp_path, CHIPSET_REL, CHIPSET)
    assert validate.validate({CHIPSET_REL}) == []


def _integrity(root: Path, *extra: str) -> tuple[int, list[str]]:
    report = root / "hard.json"
    done = subprocess.run(
        [sys.executable, "integrity_check.py", str(root), "--strict",
         "--hard-report", str(report), *extra],
        capture_output=True, text=True, encoding="utf-8", cwd=REPO,
    )
    return done.returncode, json.loads(report.read_text(encoding="utf-8"))


def test_integrity_passes_clean_parts_and_missing_folders(tmp_path) -> None:
    assert _integrity(tmp_path) == (0, [])
    _put(tmp_path, SOCKET_REL, SOCKET)
    _put(tmp_path, CHIPSET_REL, CHIPSET)
    _put(tmp_path, "socket/am2.json", {**SOCKET, "slug": "am2", "name": "Socket AM2",
                                       "aliases": []})
    _put(tmp_path, "socket/am2plus.json", {**SOCKET, "slug": "am2plus", "name": "Socket AM2+",
                                           "aliases": []})
    # The same chipset name under another manufacturer is a different part.
    _put(tmp_path, "chipset/other/other-p55.json",
         {**CHIPSET, "slug": "other-p55", "manufacturer": "other", "aliases": ["acme-p55"]})
    assert _integrity(tmp_path) == (0, [])


def test_integrity_flags_duplicate_parts(tmp_path) -> None:
    _put(tmp_path, SOCKET_REL, SOCKET)
    _put(tmp_path, "socket/lga-1156.json", {**SOCKET, "slug": "lga-1156", "name": "LGA1156",
                                            "aliases": []})
    _put(tmp_path, "socket/socket-h.json", {**SOCKET, "slug": "socket-h", "name": "Socket H",
                                            "aliases": []})
    _put(tmp_path, CHIPSET_REL, CHIPSET)
    _put(tmp_path, "chipset/acme/acme-p55-b.json", {**CHIPSET, "slug": "acme-p55-b"})
    _put(tmp_path, "chipset/acme/acme-h55.json",
         {**CHIPSET, "slug": "acme-h55", "name": "H55", "aliases": ["acme-p55"]})
    _put(tmp_path, "chipset/acme/copy/acme-h55.json",
         {**CHIPSET, "slug": "acme-h55", "name": "H55 copy"})
    code, anomalies = _integrity(tmp_path)
    assert code == 1
    for needle in (
        "[socket] DUP name 'lga1156': ['lga-1156', 'lga1156']",
        "[socket] alias 'Socket H' of lga1156 is the name or slug of socket-h",
        "[chipset] DUP name 'p55': ['acme-p55', 'acme-p55-b']",
        "[chipset] alias 'acme-p55' of acme-h55 is the name or slug of acme-p55",
        "[chipset] DUP slug acme-h55",
    ):
        assert any(needle in item for item in anomalies), (needle, anomalies)


def test_integrity_scoped_run_compares_against_the_catalog(tmp_path) -> None:
    _put(tmp_path, SOCKET_REL, SOCKET)
    _put(tmp_path, "socket/socket-h.json", {**SOCKET, "slug": "socket-h", "name": "Socket H",
                                            "aliases": []})
    _put(tmp_path, "socket/am5.json", {**SOCKET, "slug": "am5", "name": "AM5", "aliases": []})
    only = tmp_path / "only.txt"
    only.write_text("socket/am5.json\n", encoding="utf-8")
    assert _integrity(tmp_path, "--only", str(only)) == (0, [])
    only.write_text("socket/socket-h.json\n", encoding="utf-8")
    code, anomalies = _integrity(tmp_path, "--only", str(only))
    assert code == 1
    assert any("alias 'Socket H' of lga1156" in item for item in anomalies), anomalies
