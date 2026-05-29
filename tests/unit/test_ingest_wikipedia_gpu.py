"""Wikipedia GPU list-page row extractor (offline — vendored HTML)."""

from __future__ import annotations

from app.ingest.sources.wikipedia_gpu import WikipediaGpuIngest

_HTML = """
<html><body>
<h3>GeForce RTX 40 series</h3>
<table class="wikitable">
  <tr>
    <th>Model</th>
    <th>Architecture</th>
    <th>Launched</th>
    <th>Memory</th>
    <th>Type</th>
    <th>Bus</th>
    <th>Core clock</th>
    <th>Boost</th>
    <th>TDP</th>
    <th>PCIe</th>
  </tr>
  <tr>
    <td>GeForce RTX 4090</td>
    <td rowspan="2">Ada Lovelace</td>
    <td>October 12, 2022</td>
    <td>24 GB</td>
    <td>GDDR6X</td>
    <td>384-bit</td>
    <td>2235 MHz</td>
    <td>2520 MHz</td>
    <td>450 W</td>
    <td>PCIe 4.0 x16</td>
  </tr>
  <tr>
    <td>GeForce RTX 4080 Super</td>
    <td>January 31, 2024</td>
    <td>16 GB</td>
    <td>GDDR6X</td>
    <td>256-bit</td>
    <td>2295 MHz</td>
    <td>2550 MHz</td>
    <td>320 W</td>
    <td>PCIe 4.0 x16</td>
  </tr>
</table>
</body></html>
"""


def test_extracts_rtx_4090_with_full_fields() -> None:
    candidates = list(
        WikipediaGpuIngest._extract(
            _HTML, "nvidia", "List_of_Nvidia_graphics_processing_units", "NVIDIA GeForce"
        )
    )
    by_slug = {c.slug: c for c in candidates}
    assert "geforce-rtx-4090" in by_slug
    rtx = by_slug["geforce-rtx-4090"]
    assert rtx.is_complete
    assert rtx.record["memory_gb"] == 24.0
    assert rtx.record["memory_type"] == "GDDR6X"
    assert rtx.record["memory_bus_bit"] == 384
    assert rtx.record["base_clock_mhz"] == 2235
    assert rtx.record["boost_clock_mhz"] == 2520
    assert rtx.record["tdp_w"] == 450
    assert rtx.record["pcie_version"] == "4.0"
    assert rtx.record["architecture"] == "Ada Lovelace"
    assert rtx.record["release_date"] == "2022-10-12"
    assert rtx.output_path.as_posix() == "gpu/nvidia/2022/consumer/geforce-rtx-4090.json"


def test_rowspan_carries_architecture_to_second_row() -> None:
    candidates = list(
        WikipediaGpuIngest._extract(
            _HTML, "nvidia", "List_of_Nvidia_graphics_processing_units", "NVIDIA GeForce"
        )
    )
    by_slug = {c.slug: c for c in candidates}
    assert by_slug["geforce-rtx-4080-super"].record["architecture"] == "Ada Lovelace"
