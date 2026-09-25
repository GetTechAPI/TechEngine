"""Wikipedia GPU list-page row extractor (offline — vendored HTML)."""

from __future__ import annotations

from app.ingest.sources.wikipedia_gpu import (
    WikipediaGpuIngest,
    architecture_from_codename,
    normalize_bus_interface,
)

# Mirrors the real list pages: a two-row <th> header whose units live in the
# header ("Size (MiB)", "Core clock (MHz)") while body cells are bare numbers.
_HTML = """
<html><body>
<table class="wikitable">
  <tr>
    <th rowspan="2">Model</th><th rowspan="2">Launch</th><th rowspan="2">Code name</th>
    <th rowspan="2">Bus interface</th><th rowspan="2">Core clock (MHz)</th>
    <th colspan="3">Memory</th><th colspan="2">TDP (Watts)</th>
  </tr>
  <tr>
    <th>Size (MiB)</th><th>Bus type</th><th>Bus width (bit)</th><th>Idle</th><th>Max.</th>
  </tr>
  <tr>
    <th>Radeon HD 5870 Eyefinity Edition<sup>2</sup></th><td>Mar 11, 2010</td>
    <td>Cypress XT</td><td>PCIe 2.1 ×16</td><td>850</td>
    <td>2048</td><td>GDDR5</td><td>256</td><td>27</td><td>228</td>
  </tr>
  <tr>
    <th>Radeon HD 6970</th><td>December 15, 2010</td>
    <td>Cayman XT</td><td>PCIe 2.1 ×16</td><td>880</td>
    <td>2048</td><td>GDDR5</td><td>256</td><td>20</td><td>250</td>
  </tr>
  <tr>
    <th>Radeon HD 4200</th><td>March 2, 2010</td>
    <td>RS880</td><td>IGP</td><td>500</td>
    <td>128</td><td>DDR2</td><td>64</td><td>1</td><td>15</td>
  </tr>
  <tr>
    <th>Radeon HD 5970</th><td>November 18, 2009</td>
    <td>2× Hemlock XT</td><td>PCIe 2.1 ×16</td><td>725</td>
    <td>2× 1024</td><td>GDDR5</td><td>2× 256</td><td>51</td><td>294</td>
  </tr>
</table>
</body></html>
"""


def _extract() -> dict[str, object]:
    candidates = WikipediaGpuIngest._extract(
        _HTML, "amd", "List_of_AMD_graphics_processing_units", "AMD Radeon"
    )
    return {c.slug: c for c in candidates}


def test_multirow_header_with_header_units() -> None:
    card = _extract()["radeon-hd-5870-eyefinity-edition"]
    assert card.is_complete  # type: ignore[attr-defined]
    assert card.record == card.record | {  # type: ignore[attr-defined]
        "architecture": "TeraScale 2",
        "release_date": "2010-03-11",
        "memory_gb": 2.0,
        "memory_type": "GDDR5",
        "memory_bus_bit": 256,
        "base_clock_mhz": 850,
        "boost_clock_mhz": 850,  # pre-boost board: boost == base
        "tdp_w": 228,  # Max., not Idle
        "pcie_version": "PCIe 2.1 x16",
    }
    # Launched before the Oct 2010 rebrand → filed under ATI.
    assert card.output_path.as_posix() == (  # type: ignore[attr-defined]
        "gpu/ati/2010/consumer/radeon-hd-5870-eyefinity-edition.json"
    )


def test_post_rebrand_card_stays_amd() -> None:
    card = _extract()["radeon-hd-6970"]
    assert card.record["architecture"] == "TeraScale 3"  # type: ignore[attr-defined]
    assert card.output_path.as_posix().startswith("gpu/amd/2010/")  # type: ignore[attr-defined]


def test_skips_igp_and_dual_gpu_rows() -> None:
    slugs = _extract()
    assert "radeon-hd-4200" not in slugs
    assert "radeon-hd-5970" not in slugs


def test_codename_and_bus_helpers() -> None:
    assert architecture_from_codename("NV34GL", "nvidia") == "Rankine"
    assert architecture_from_codename("GK104", "nvidia") == "Kepler"
    assert architecture_from_codename("RV620 PRO", "amd") == "TeraScale"
    assert architecture_from_codename("Rage 4", "amd") is None
    assert normalize_bus_interface("AGP 8×") == "AGP 8x"
    assert normalize_bus_interface("AGP 4× PCI") == "AGP 4x"
    assert normalize_bus_interface("IGP") is None


_RDNA_HTML = """
<table class="wikitable">
  <tr><th rowspan="2">Model</th><th rowspan="2">Launch</th><th rowspan="2">Architecture</th>
      <th rowspan="2">Bus interface</th><th colspan="2">Core</th><th colspan="3">Memory</th>
      <th rowspan="2">TDP</th></tr>
  <tr><th>Clock (MHz)</th><th>Config</th><th>Size (GB)</th><th>Bus type</th>
      <th>Bus width (bit)</th></tr>
  <tr><th>Radeon Pro W7900 Dual Slot</th><td>June 19, 2024</td><td>RDNA 3</td>
      <td>PCIe 4.0 ×16</td><td>1855 2495</td><td>6144:384:192</td><td>48</td><td>GDDR6</td>
      <td>384</td><td>295 W</td></tr>
  <tr><th>Radeon RX 6300M</th><td>January 4, 2022</td><td>RDNA 2</td>
      <td>PCIe 4.0 ×4</td><td>1512</td><td>768:48:32</td><td>2</td><td>GDDR6</td>
      <td>32</td><td>25 W</td></tr>
</table>
"""


def test_game_clock_cells_do_not_become_base_and_boost() -> None:
    page = "List_of_AMD_graphics_processing_units"
    cards = {c.slug: c for c in WikipediaGpuIngest._extract(_RDNA_HTML, "amd", page, "")}
    assert cards["radeon-pro-w7900-dual-slot"].record["base_clock_mhz"] is None
    assert cards["radeon-rx-6300m"].record["boost_clock_mhz"] is None
    assert not cards["radeon-rx-6300m"].is_complete
