"""GPU collectors write a value only when the cell states it unambiguously."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.ingest.gpu_identity import gpu_family_key
from app.ingest.pipeline import run
from app.ingest.sources.base import IngestCandidate
from app.ingest.sources.wikipedia_gpu import (
    _build_candidate,
    architecture_from_codename,
    classify_column,
    normalize_bus_interface,
)

_CELLS = {
    "model": ("GeForce GTX 1070", ""),
    "release_date": ("June 10, 2016", ""),
    "codename": ("GP104", ""),
    "bus": ("PCIe 3.0 ×16", ""),
    "base_clock": ("1506", "mhz"),
    "boost_clock": ("1683", "mhz"),
    "memory_size": ("8", "gb"),
    "memory_type": ("GDDR5", ""),
    "memory_bus": ("256", ""),
    "tdp": ("150", ""),
}


def _card(
    headings: tuple[str, ...] = (), manufacturer: str = "nvidia", **cells: str | tuple[str, str]
) -> IngestCandidate | None:
    merged = dict(_CELLS)
    for key, value in cells.items():
        merged[key] = value if isinstance(value, tuple) else (value, merged.get(key, ("", ""))[1])
    return _build_candidate(manufacturer, merged, "https://example.test/gpus", headings)


def test_plain_row_keeps_every_value() -> None:
    card = _card()
    assert card is not None and card.is_complete and not card.problems
    assert card.record["pcie_version"] == "PCIe 3.0 x16"
    assert card.record["memory_gb"] == 8.0


@pytest.mark.parametrize(
    "label,expected",
    [
        ("Memory Configuration / DRAM Type", "memory_type"),
        ("Memory / Type", "memory_type"),
        ("Clock speed / Core ( MHz )", "base_clock"),
        ("Clock rate / Base core ( MHz )", "base_clock"),
        ("Clock Rate / Base (Boost) ( MHz )", "base_boost"),
        ("Clock Rate / Shader (Boost) ( MHz )", None),
    ],
)
def test_classify_column_labels(label: str, expected: str | None) -> None:
    assert classify_column(label) == expected


def test_base_boost_cell_gives_both_clocks() -> None:
    card = _card(base_clock="", boost_clock="", base_boost=("1291 (1392)", "mhz"))
    assert card is not None
    assert (card.record["base_clock_mhz"], card.record["boost_clock_mhz"]) == (1291, 1392)


@pytest.mark.parametrize(
    "key,value,field",
    [
        ("memory_type", "GDDR3 GDDR5", "memory_type"),
        ("memory_bus", "128 256", "memory_bus_bit"),
        ("memory_size", "512 1024", "memory_gb"),
        ("bus", "AGP 8× PCIe ×16", "pcie_version"),
        ("release_date", "June 10, 2016 August 2, 2016", "release_date"),
        ("release_date", "2016", "release_date"),
        ("release_date", "June 2016?", "release_date"),
        # RTX 40 laptop "Base core" ranges: neither end is the part's clock.
        ("base_clock", "1455–2175", "base_clock_mhz"),
        ("base_clock", "1506 1607", "base_clock_mhz"),
        ("boost_clock", "~1700", "boost_clock_mhz"),
        ("tdp", "100-260", "tdp_w"),
        ("tdp", "~75", "tdp_w"),
        ("tdp", "45?", "tdp_w"),
        ("memory_type", "SGRAM, SDR", "memory_type"),
        ("memory_type", "EDO, SGRAM, SDR", "memory_type"),
    ],
)
def test_multi_value_range_and_estimate_cells_give_none(key: str, value: str, field: str) -> None:
    card = _card(**{key: value})
    assert card is not None
    assert card.record[field] is None


@pytest.mark.parametrize(
    "key,value,field,expected",
    [
        ("memory_size", ("Up to 512", "mib"), "memory_gb", 0.5),
        ("memory_size", ("32", "mib"), "memory_gb", 0.03125),
        ("memory_size", ("128 + shared", "mib"), "memory_gb", 0.125),
        ("tdp", "<75", "tdp_w", 75),
        ("tdp", "Up to 150", "tdp_w", 150),
        ("release_date", "Feb, 2024", "release_date", "2024-02-01"),
        ("memory_bus", "1\u2009024", "memory_bus_bit", 1024)  # thin space,
    ],
)
def test_bounds_and_spellings_that_state_one_value(
    key: str, value: str | tuple[str, str], field: str, expected: object
) -> None:
    card = _card(**{key: value})
    assert card is not None
    assert card.record[field] == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("PCIe 2.0 ×16", "PCIe 2.0 x16"),
        ("PCIe x16 2.0", "PCIe 2.0 x16"),
        ("PCIe 2x16", "PCIe 2.0 x16"),
        ("PCI-E 1.0 x16", "PCIe 1.0 x16"),
        ("PCIe 2.0 ×16 (MXM)", "PCIe 2.0 x16"),
        ("PCIe 3.0 ×8 ×16", "PCIe 3.0"),
        ("AGP 8×", "AGP 8x"),
        ("AGP 4× PCI", None),
        ("AGP 8× PCIe ×16", None),
        ("ISA, EISA, VLB, PCI, MCA", None),
        ("ISA, VLB, PCI", None),
        ("AGP 4x,8x", None),
        ("IGP", None),
    ],
)
def test_normalize_bus_interface(text: str, expected: str | None) -> None:
    assert normalize_bus_interface(text) == expected


@pytest.mark.parametrize(
    "cells,headings",
    [
        ({"memory_size": "Up to 256 from system memory"}, ()),
        ({"bus": "HyperTransport"}, ()),
        ({"bus": "Internal"}, ()),
        ({}, ("IGP (Radeon HD 4000 series)", "Chipsets")),
        ({"model": "Radeon 530X Radeon 535"}, ()),
        ({"model": "Radeon R9 M395X Amethyst XT)"}, ()),
    ],
)
def test_igp_and_split_cell_rows_are_dropped(
    cells: dict[str, str], headings: tuple[str, ...]
) -> None:
    assert _card(headings, **cells) is None


def test_laptop_table_rows_need_a_laptop_name() -> None:
    notebook = _card(("Mobile GPUs",), model="GeForce GTX 1070 (Notebook)")
    assert notebook is not None and notebook.slug == "geforce-gtx-1070-mobile"
    assert not notebook.problems
    for model in ("GeForce GTX 980M", "Quadro M1000M", "GeForce MX150", "GeForce RTX 4070 Max-Q"):
        card = _card(("Mobile GPUs",), model=model)
        assert card is not None and not card.problems, model
    bare = _card(("Mobile GPUs",), model="GeForce GTX 1060")
    assert bare is not None
    assert bare.problems == ("laptop table row without a laptop marker in its name",)


def test_rv200_has_no_architecture() -> None:
    assert architecture_from_codename("RV200", "amd") is None


@pytest.mark.parametrize(
    "left,right",
    [
        ("geforce-gtx-1070-oem", "geforce-gtx-1070"),
        ("tesla-k20", "k20-gpu-accelerator"),
        ("tesla-c2070", "c2070-gpu-computing-module"),
        ("tesla-m6-mobile", "m6-gpu-accelerator"),
        ("radeon-instinct-mi100", "instinct-mi100"),
        ("quadro-nvs-50-pci", "nvs-50"),
        ("radeon-e9172-mxm", "radeon-e9172"),
        ("geforce-gtx-1070-notebook", "geforce-gtx-1070-mobile"),
        ("quadro4-200-nvs", "nvs-200"),
        ("radeon-x800-xt-platinum-agp", "radeon-x800-xt-pe"),
        ("rtx-500-mobile-ada-generation", "rtx-500-ada-mobile"),
    ],
)
def test_family_key_folds_names_of_one_board(left: str, right: str) -> None:
    assert gpu_family_key(left) == gpu_family_key(right)


def test_family_key_keeps_the_laptop_tier() -> None:
    assert gpu_family_key("geforce-gtx-1660-ti-mobile") != gpu_family_key("geforce-gtx-1660-ti")


def _gpu(slug: str, released: str = "2013-01-01") -> IngestCandidate:
    return IngestCandidate(
        category="gpu",
        manufacturer="nvidia",
        slug=slug,
        record={"slug": slug, "name": slug, "base_clock_mhz": 700, "tdp_w": 225},
        source_url=f"https://example.test/{slug}",
        output_path=Path("gpu/nvidia") / released[:4] / "enterprise" / f"{slug}.json",
    )


def _curate(root: Path, *slugs: str) -> None:
    for slug in slugs:
        target = root / "gpu/nvidia/2013/enterprise" / f"{slug}.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"slug": slug}), encoding="utf-8")


def test_datacenter_family_row_is_skipped_when_curated_has_only_its_form_factors(
    tmp_path: Path,
) -> None:
    _curate(tmp_path, "tesla-k20c", "tesla-k20m", "tesla-k20x")
    family = _gpu("k20-gpu-accelerator")
    result = run([family], data_root=tmp_path, run_date=date(2026, 11, 2))
    assert result.skipped_existing == [family]
    assert result.reason(family).startswith("names the board family of ")


def test_laptop_part_of_a_curated_desktop_card_is_added(tmp_path: Path) -> None:
    _curate(tmp_path, "geforce-gtx-1660-ti")
    laptop = _gpu("geforce-gtx-1660-ti-mobile")
    result = run([laptop], data_root=tmp_path, run_date=date(2026, 11, 2))
    assert result.written == [laptop]


def test_name_leaves_the_brand_to_manufacturer() -> None:
    card = _card()
    assert card is not None
    assert card.record["name"] == "GeForce GTX 1070"
    prefixed = _card(model="NVIDIA GeForce GTX 1070")
    assert prefixed is not None and prefixed.record["name"] == "GeForce GTX 1070"


@pytest.mark.parametrize(
    "launch",
    ["May 1, 2007 (PCIe) October 1, 2007 (AGP)", "2004?", "2007 (2008)"],
)
def test_amd_page_board_dated_before_the_rebrand_by_every_date_is_ati(launch: str) -> None:
    card = _card(manufacturer="amd", model="Radeon X1650 GT", release_date=launch)
    assert card is not None
    assert card.manufacturer == "ati"
    assert card.record["release_date"] is None
    assert "no release date to file it under" in card.problems


def test_laptop_table_row_without_mobile_in_its_name_is_checked_as_the_mobile_part(
    tmp_path: Path,
) -> None:
    card = _card(("Quadro M", "Mobile applications"), model="Quadro M5500")
    assert card is not None and card.aliases == ("quadro-m5500-mobile",)
    target = tmp_path / "gpu/nvidia/2016/enterprise/quadro-m5500-mobile.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"slug": "quadro-m5500-mobile"}), encoding="utf-8")
    result = run([card], data_root=tmp_path, include_drafts=True, run_date=date(2026, 11, 2))
    assert result.skipped_existing == [card]


@pytest.mark.parametrize(
    "codename,expected",
    [
        ("GT216", "Tesla 2.0"),
        ("G92", "Tesla"),
        ("GF116", "Fermi 2.0"),
        ("GF100", "Fermi"),
        ("GK208", "Kepler 2.0"),
        ("GK104", "Kepler"),
    ],
)
def test_second_generation_architectures(codename: str, expected: str) -> None:
    assert architecture_from_codename(codename, "nvidia") == expected
