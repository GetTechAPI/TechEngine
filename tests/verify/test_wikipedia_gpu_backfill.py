"""Offline gates for the Wikipedia GPU backfill. No network."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.verify.crossref import _heading_matches
from app.verify.wikipedia_gpu_backfill import (
    KAGGLE_GPU_URL,
    CardName,
    WikiRow,
    backfill,
    decide,
    form_factor_marks,
    interface_families,
    memory_close,
    rows_from_html,
    split_card_name,
)

_HTML = """
<html><head><title>Quadro - Wikipedia</title></head><body>
<h3>Desktop AGP</h3>
<table class="wikitable">
  <tr>
    <th>Model</th><th>Launch</th><th>Memory size</th>
    <th>Core clock</th><th>Power max.</th><th>Interface</th><th>Bus width</th>
  </tr>
  <tr>
    <td>Quadro4 100 NVS</td><td>December 22, 2003</td><td>64 MB</td>
    <td>250 MHz</td><td>10 W</td><td>AGP 4x</td><td>128-bit</td>
  </tr>
  <tr>
    <td>Quadro4 200 NVS</td><td>December 22, 2003</td><td>64 MB</td>
    <td>275 MHz</td><td>11 W</td><td>AGP 4x</td><td>128-bit</td>
  </tr>
</table>
<h3>Voodoo Banshee</h3>
<p>Near the end of 1998, 3dfx released the Voodoo Banshee.</p>
</body></html>
"""


def _rec(**overrides: object) -> dict:
    record = {
        "slug": "quadro4-100-nvs",
        "name": "Quadro4 100 NVS",
        "manufacturer": "nvidia",
        "release_date": "2003-12-22",
        "memory_gb": 0.0625,
        "memory_bus_bit": 128,
        "base_clock_mhz": 250,
        "tdp_w": 10,
        "pcie_version": "AGP 4x",
        "source_urls": [KAGGLE_GPU_URL],
    }
    record.update(overrides)
    return record


def test_split_strips_memory_and_bus_suffixes() -> None:
    banshee = split_card_name("Voodoo Banshee AGP 16 MB")
    assert banshee.base == "Voodoo Banshee"
    assert banshee.interface == "agp"
    assert memory_close(banshee.memory_gb or 0, 0.016)

    quadro = split_card_name("Quadro4 100 NVS PCI")
    assert quadro == CardName("Quadro4 100 NVS PCI", "Quadro4 100 NVS", None, "pci")

    riva = split_card_name("Riva 128 PCI")
    assert riva.base == "Riva 128"
    assert riva.interface == "pci"
    assert _heading_matches(riva.base, "RIVA 128")

    assert split_card_name("GeForce FX 5700 Ultra").base == "GeForce FX 5700 Ultra"
    pcie = split_card_name("FirePro 2270 PCIe x1")
    assert pcie.base == "FirePro 2270"
    assert pcie.interface == "pcie"
    passive = split_card_name("FirePro S10000 Passive 12GB")
    assert passive.base == "FirePro S10000 Passive"
    assert passive.memory_gb == 12


def test_interface_families_do_not_treat_pcie_as_pci() -> None:
    assert interface_families("PCIe 2.0 x16") == frozenset({"pcie"})
    assert interface_families("PCI") == frozenset({"pci"})
    assert interface_families("AGP Pro 8x") == frozenset({"agp"})
    assert interface_families("AGP/PCI") == frozenset({"agp", "pci"})


def test_table_row_confirms_on_memory_and_not_on_name_alone() -> None:
    rows = rows_from_html(_HTML, "Quadro", "https://en.wikipedia.org/wiki/Quadro")
    models = {row.model for row in rows}
    assert "Quadro4 100 NVS" in models
    assert "Voodoo Banshee" in models

    confirmed = decide(_rec(), rows)
    assert confirmed.decision == "confirm"
    assert "memory_gb" in confirmed.agreements
    assert confirmed.proposed_url is not None
    assert "en.wikipedia.org/wiki/Quadro" in confirmed.proposed_url

    name_only = WikiRow(
        model="Quadro4 100 NVS", url="https://en.wikipedia.org/wiki/Quadro", page="Quadro"
    )
    assert decide(_rec(), [name_only]).decision == "ambiguous"
    assert decide(_rec(), [name_only]).reason == "no-comparable-spec"


def test_wrong_memory_is_not_a_confirm() -> None:
    rows = rows_from_html(_HTML, "Quadro", "https://en.wikipedia.org/wiki/Quadro")
    outcome = decide(_rec(memory_gb=0.25), rows)
    assert outcome.decision in {"ambiguous", "contradict"}
    assert outcome.decision != "confirm"


def test_name_suffix_must_agree_with_the_record() -> None:
    rows = rows_from_html(_HTML, "Quadro", "https://en.wikipedia.org/wiki/Quadro")
    # Name says 32 MB; the record (and the Wikipedia row) say 64 MB.
    outcome = decide(_rec(name="Quadro4 100 NVS 32 MB"), rows)
    assert outcome.decision != "confirm"
    assert "name_memory_gb" in outcome.conflicts


def test_pci_record_does_not_confirm_against_an_agp_row() -> None:
    rows = rows_from_html(_HTML, "Quadro", "https://en.wikipedia.org/wiki/Quadro")
    outcome = decide(_rec(name="Quadro4 100 NVS PCI", pcie_version="PCI"), rows)
    assert outcome.decision != "confirm"


def test_memory_suffix_is_not_confirmed_by_year_alone() -> None:
    rows = rows_from_html(_HTML, "3dfx", "https://en.wikipedia.org/wiki/3dfx")
    record = _rec(
        name="Voodoo Banshee AGP 16 MB",
        memory_gb=0.016,
        pcie_version="AGP 1x",
        release_date="1998-01-01",
        tdp_w=15,
        base_clock_mhz=100,
        memory_bus_bit=128,
    )
    outcome = decide(record, rows)
    assert outcome.decision == "ambiguous"
    assert outcome.reason == "memory-suffix-unconfirmed"
    assert outcome.title == "Voodoo Banshee"


_BARE_MB = """
<table class="wikitable">
  <tr><th>Model</th><th>Launch</th><th>Memory</th><th>Bus interface</th></tr>
  <tr><th></th><th></th><th>Size (MB)</th><th></th></tr>
  <tr>
    <td>Radeon HD 6450 (Caicos)</td><td>April 7, 2011</td><td>512</td><td>PCIe 2.1 x16</td>
  </tr>
</table>
"""


def test_codename_and_bare_megabytes_confirm() -> None:
    rows = rows_from_html(
        _BARE_MB, "List_of_AMD_graphics_processing_units", "https://en.wikipedia.org/wiki/List"
    )
    assert any(row.model.startswith("Radeon HD 6450") for row in rows)
    record = _rec(
        name="Radeon HD 6450",
        memory_gb=0.5,
        pcie_version="PCIe 2.0 x16",
        release_date="2011-04-07",
        tdp_w=18,
        base_clock_mhz=625,
        memory_bus_bit=64,
    )
    outcome = decide(record, rows)
    assert outcome.decision == "confirm"
    assert "memory_gb" in outcome.agreements
    assert outcome.suffix_only is False


def test_missing_heading_is_notfound() -> None:
    rows = rows_from_html(_HTML, "Quadro", "https://en.wikipedia.org/wiki/Quadro")
    outcome = decide(_rec(name="Imaginary GPU 9000"), rows)
    assert outcome.decision == "notfound"


def test_sibling_sku_is_not_a_suffix_confirm() -> None:
    rows = rows_from_html(_HTML, "Quadro", "https://en.wikipedia.org/wiki/Quadro")
    outcome = decide(_rec(name="Quadro4 200 NVS", tdp_w=11, base_clock_mhz=275), rows)
    assert outcome.decision == "confirm"
    assert outcome.title == "Quadro4 200 NVS"
    other = decide(_rec(name="Quadro4 200 NVS", tdp_w=10, base_clock_mhz=250), rows)
    # 10 W / 250 MHz is the 100 NVS row, which does not heading-match 200 NVS.
    assert other.title == "Quadro4 200 NVS"
    assert other.decision != "confirm"


def test_dry_run_does_not_touch_techapi_json(tmp_path: Path) -> None:
    data = tmp_path / "TechAPI" / "data" / "gpu" / "nvidia" / "2003"
    data.mkdir(parents=True)
    target = data / "quadro4-100-nvs.json"
    record = _rec()
    raw = json.dumps(record, indent=2) + "\n"
    target.write_text(raw, encoding="utf-8")
    calls: list[str] = []

    def fetch(page: str) -> tuple[int, str, str]:
        calls.append(page)
        return 200, f"https://en.wikipedia.org/wiki/{page}", _HTML

    result = backfill(
        data_root=tmp_path / "TechAPI",
        cache_path=tmp_path / "cache.jsonl",
        summary_path=tmp_path / "summary.md",
        limit=5,
        sleep_s=1.0,
        dry_run=True,
        apply=False,
        max_fallback=0,
        pages=[("nvidia", "Quadro", "NVIDIA Quadro")],
        fetch_page=fetch,
        search_fn=lambda _name: [],
    )
    assert target.read_text(encoding="utf-8") == raw
    assert result.counts()["confirm"] == 1
    assert "CONFIRM" in (tmp_path / "summary.md").read_text(encoding="utf-8")
    assert calls == ["Quadro"]


def test_apply_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SystemExit, match="refusing --apply"):
        backfill(
            data_root=tmp_path,
            cache_path=tmp_path / "cache.jsonl",
            summary_path=tmp_path / "summary.md",
            limit=1,
            sleep_s=1.0,
            dry_run=True,
            apply=True,
        )


def test_form_factor_marks_ignore_memory_units_and_mx() -> None:
    assert form_factor_marks("Radeon 9600") == frozenset()
    assert form_factor_marks("Radeon 9600 128 MB") == frozenset()
    assert form_factor_marks("GeForce MX150") == frozenset()
    assert form_factor_marks("Mobility Radeon 9600") == frozenset({"mobility"})
    assert form_factor_marks("Radeon HD 6330M") == frozenset({"m-suffix"})
    assert form_factor_marks("GeForce2 Go 100") == frozenset({"go"})
    assert form_factor_marks("GeForce RTX 2080 Max-Q") == frozenset({"max-q"})
    assert form_factor_marks("라데온 모바일 9600") == frozenset({"mobile"})
    assert form_factor_marks("Mobility_Radeon_series") == frozenset({"mobility"})
    assert form_factor_marks("Radeon_HD_6000M_series") == frozenset({"m-suffix"})


def _spec_row(model: str, **overrides: object) -> WikiRow:
    row = WikiRow(
        model=model,
        url="https://en.wikipedia.org/wiki/List#" + model.replace(" ", "_"),
        page="List",
        memory_gb=(0.0625,),
        year=2003,
        interfaces=frozenset({"agp"}),
    )
    for key, value in overrides.items():
        object.__setattr__(row, key, value)
    return row


def test_desktop_radeon_does_not_confirm_mobility_row() -> None:
    mobility = _spec_row(
        "Mobility Radeon 9600",
        url="https://en.wikipedia.org/wiki/List_of_AMD_graphics_processing_units#Mobility_Radeon_series",
        section="Mobility Radeon series",
    )
    desktop = _spec_row(
        "Radeon 9600",
        url="https://en.wikipedia.org/wiki/List_of_AMD_graphics_processing_units#AGP_(9000_series)",
        section="AGP (9000 series)",
    )
    record = _rec(
        name="Radeon 9600",
        memory_gb=0.0625,
        pcie_version="AGP 8x",
        release_date="2003-10-01",
    )
    only_mobile = decide(record, [mobility])
    assert only_mobile.decision == "ambiguous"
    assert only_mobile.reason == "form-factor-variant"
    assert only_mobile.proposed_url is None

    both = decide(record, [mobility, desktop])
    assert both.decision == "confirm"
    assert both.title == "Radeon 9600"

    # The marker is on the section URL even when the model cell omits it.
    hidden = _spec_row(
        "Radeon 9600",
        url="https://en.wikipedia.org/wiki/List#Mobility_Radeon_series",
        section="Mobility Radeon series",
    )
    assert decide(record, [hidden]).reason == "form-factor-variant"


def test_mobile_record_still_confirms_its_own_row() -> None:
    row = _spec_row("Mobility Radeon 7500", section="Mobility Radeon series")
    record = _rec(
        name="Mobility Radeon 7500",
        memory_gb=0.0625,
        pcie_version="AGP 4x",
        release_date="2003-01-01",
    )
    outcome = decide(record, [row])
    assert outcome.decision == "confirm"
    assert outcome.title == "Mobility Radeon 7500"


def test_go_and_max_q_markers_block_a_one_sided_match() -> None:
    go = _spec_row("GeForce2 Go", section="GeForce2 Go series")
    plain = _rec(
        name="GeForce2 Go",
        memory_gb=0.0625,
        pcie_version="AGP 4x",
        release_date="2003-01-01",
    )
    assert decide(plain, [go]).decision == "confirm"
    # Same model text, but the section URL is the laptop Go line.
    in_go_section = _spec_row(
        "GeForce2",
        url="https://en.wikipedia.org/wiki/List#GeForce2_Go_series",
        section="GeForce2 Go series",
    )
    bare = _rec(
        name="GeForce2",
        memory_gb=0.0625,
        pcie_version="AGP 4x",
        release_date="2003-01-01",
    )
    blocked = decide(bare, [in_go_section])
    assert blocked.decision == "ambiguous"
    assert blocked.reason == "form-factor-variant"
    desktop = _spec_row("GeForce2", section="GeForce2 series")
    assert decide(bare, [desktop]).decision == "confirm"

    maxq = _spec_row("Max-Q GeForce RTX 2080", year=2019, memory_gb=(8,))
    laptop = _rec(
        name="GeForce RTX 2080",
        memory_gb=8,
        pcie_version="PCIe 3.0 x16",
        release_date="2019-01-01",
    )
    assert decide(laptop, [maxq]).reason == "form-factor-variant"


def test_m_suffix_on_only_one_title_is_not_a_confirm() -> None:
    record = _rec(
        name="Radeon HD 6330",
        memory_gb=0.0625,
        pcie_version="PCIe 2.0 x16",
        release_date="2011-01-01",
    )
    # Section anchor carries 6000M; the model cell itself matches.
    mobile = _spec_row(
        "Radeon HD 6330",
        url="https://en.wikipedia.org/wiki/List#Radeon_HD_6000M_series",
        section="Radeon HD 6000M series",
        year=2011,
        interfaces=frozenset({"pcie"}),
    )
    assert decide(record, [mobile]).reason == "form-factor-variant"
    own = _spec_row(
        "Radeon HD 6330M",
        url="https://en.wikipedia.org/wiki/List#Radeon_HD_6000M_series",
        section="Radeon HD 6000M series",
        year=2011,
        interfaces=frozenset({"pcie"}),
    )
    named = _rec(
        name="Radeon HD 6330M",
        memory_gb=0.0625,
        pcie_version="PCIe 2.0 x16",
        release_date="2011-01-01",
    )
    assert decide(named, [own]).decision == "confirm"


def test_year_alone_is_not_a_confirm() -> None:
    row = WikiRow(
        model="Radeon HD 6250",
        url="https://en.wikipedia.org/wiki/List#IGP_(HD_6000)",
        page="List",
        section="IGP (HD 6000)",
        year=2011,
    )
    record = _rec(name="Radeon HD 6250", memory_gb=0.5, release_date="2011-01-31", tdp_w=19)
    outcome = decide(record, [row])
    assert outcome.decision == "ambiguous"
    assert outcome.reason == "insufficient-specs"
    assert outcome.proposed_url is None


def test_pcie_record_does_not_confirm_an_agp_section() -> None:
    html = """
    <h3>AGP (X7xx, X8xx)</h3>
    <table class="wikitable">
      <tr><th>Model</th><th>Launch</th><th>Memory size</th></tr>
      <tr><td>Radeon X800 Pro</td><td>May 4, 2004</td><td>256 MB</td></tr>
    </table>
    <h3>PCIe (X8xx)</h3>
    <table class="wikitable">
      <tr><th>Model</th><th>Launch</th><th>Memory size</th></tr>
      <tr><td>Radeon X800 Pro</td><td>May 4, 2004</td><td>256 MB</td></tr>
    </table>
    """
    rows = rows_from_html(html, "List", "https://en.wikipedia.org/wiki/List")
    record = _rec(
        name="Radeon X800 PRO",
        memory_gb=0.25,
        pcie_version="PCIe 1.0 x16",
        release_date="2004-05-01",
        memory_bus_bit=256,
        base_clock_mhz=475,
        tdp_w=48,
    )
    outcome = decide(record, rows)
    assert outcome.decision == "confirm"
    assert outcome.proposed_url is not None
    assert "PCIe" in outcome.proposed_url
    agp_only = [row for row in rows if row.section_interface == "agp"]
    blocked = decide(record, agp_only)
    assert blocked.decision != "confirm"
    assert "section_interface" in blocked.conflicts


def test_same_name_core_variants_stay_ambiguous() -> None:
    shared = dict(memory_gb=(0.125,), year=2005, interfaces=frozenset({"pcie"}), page="List")
    rows = [
        WikiRow(
            model="Radeon X300 (RV370)",
            url="https://en.wikipedia.org/wiki/List#a",
            section="PCIe",
            **shared,
        ),
        WikiRow(
            model="Radeon X300 (RV380)",
            url="https://en.wikipedia.org/wiki/List#b",
            section="PCIe",
            **shared,
        ),
    ]
    record = _rec(
        name="Radeon X300",
        memory_gb=0.125,
        pcie_version="PCIe 1.0 x16",
        release_date="2005-01-01",
    )
    outcome = decide(record, rows)
    assert outcome.decision == "ambiguous"
    assert outcome.reason == "multiple-rows"
    one = decide(record, rows[:1])
    assert one.decision == "confirm"
    assert "RV370" in (one.title or "")


def test_apply_writes_only_confirmed_records(tmp_path: Path) -> None:
    data = tmp_path / "TechAPI" / "data" / "gpu" / "nvidia" / "2003"
    data.mkdir(parents=True)
    confirm_path = data / "quadro4-100-nvs.json"
    other_path = data / "imaginary-gpu-9000.json"
    confirm_raw = json.dumps(_rec(), indent=2) + "\n"
    other_record = _rec(slug="imaginary-gpu-9000", name="Imaginary GPU 9000")
    other_raw = json.dumps(other_record, indent=2) + "\n"
    confirm_path.write_text(confirm_raw, encoding="utf-8")
    other_path.write_text(other_raw, encoding="utf-8")

    result = backfill(
        data_root=tmp_path / "TechAPI",
        cache_path=tmp_path / "cache.jsonl",
        summary_path=tmp_path / "summary.md",
        limit=5,
        sleep_s=1.0,
        dry_run=False,
        apply=True,
        max_fallback=0,
        pages=[("nvidia", "Quadro", "NVIDIA Quadro")],
        fetch_page=lambda page: (200, f"https://en.wikipedia.org/wiki/{page}", _HTML),
        search_fn=lambda _name: [],
    )
    written = json.loads(confirm_path.read_text(encoding="utf-8"))
    assert result.counts()["confirm"] == 1
    assert result.written == 1
    assert written["source_urls"][0] == KAGGLE_GPU_URL
    assert any("en.wikipedia.org/wiki/Quadro" in url for url in written["source_urls"])
    assert other_path.read_text(encoding="utf-8") == other_raw
