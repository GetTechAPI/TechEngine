"""CPU collectors write a value only when the cell states it unambiguously."""

from __future__ import annotations

import pytest
from bs4 import BeautifulSoup

from app.ingest.cpu_identity import cpu_key
from app.ingest.sources.base import IngestCandidate
from app.ingest.sources.cpu_tables import _clock_value, _field, parse_cpu_table
from app.ingest.sources.wikipedia_cpu import PAGES, WikipediaCpuIngest, architecture_from_heading


def _extract(html: str, manufacturer: str = "amd") -> dict[str, IngestCandidate]:
    page = "List_of_AMD_Ryzen_processors" if manufacturer == "amd" else "List_of_Intel_Core"
    family = "AMD Ryzen" if manufacturer == "amd" else "Intel Core"
    return {c.slug: c for c in WikipediaCpuIngest._extract(html, manufacturer, page, family)}


def _rows(html: str) -> list[dict[str, str]]:
    return list(parse_cpu_table(BeautifulSoup(html, "html.parser").find("table")))


@pytest.mark.parametrize(
    "label,expected",
    [
        ("TDP (W)", "tdp"),
        ("TDP / Base", "tdp"),
        ("Power / Default", "tdp"),
        ("TDP / Nominal", "tdp"),
        ("TDP / Turbo", None),
        ("cTDP / Down", None),
        ("Configurable TDP", None),
        ("TDP (W) / E", None),
        ("Clock (GHz) / Base", "base_clock"),
        ("Clock (GHz) / Boost", "boost_clock"),
        ("Clock (GHz) / E", None),
        ("Clock rate / LP-E", None),
        ("Base clock / Min", None),
        ("Clock / cTDP down", None),
        ("Memory / Clock (MT/s)", None),
        ("iGPU / Clock", None),
        ("IGP / Base", None),
        ("L3 cache (MiB)", "l3_cache"),
        ("L3 per CCX", None),
    ],
)
def test_field_maps_only_unambiguous_labels(label: str, expected: str | None) -> None:
    assert _field(label) == expected


@pytest.mark.parametrize(
    "value,field,expected",
    [
        # Zen 4 and Zen 4c clocks in one cell: the max boost, and no single base.
        ("5.0 / 3.7", "boost_clock", "5.0"),
        ("5.0 / 3.7", "base_clock", ""),
        # PBO with 1–2 cores, then with more.
        ("4.0 (3.7)", "boost_clock", "4.0"),
        # Core counts under a misaligned clock header stay as they are.
        ("4 (4)", "boost_clock", "4 (4)"),
        ("3.6", "base_clock", "3.6"),
    ],
)
def test_clock_cells(value: str, field: str, expected: str) -> None:
    assert _clock_value(value, field) == expected


def test_boost_is_the_highest_boost_column() -> None:
    rows = _rows(
        """<table>
        <tr><th rowspan="2">Model</th><th colspan="3">Clock (GHz)</th></tr>
        <tr><th>Base</th><th>All-core turbo</th><th>Max turbo</th></tr>
        <tr><td>i5-9600K</td><td>3.7</td><td>4.3</td><td>4.6</td></tr>
        </table>"""
    )
    assert rows[0]["base_clock"] == "3.7 GHz"
    assert rows[0]["boost_clock"] == "4.6 GHz"


def test_lone_cell_is_a_caption_only_when_it_spans_the_table() -> None:
    rows = _rows(
        """<table>
        <tr><th rowspan="2">Model</th><th rowspan="2">Cores</th>
        <th colspan="2">Turbo Boost</th></tr>
        <tr><th colspan="2">2.0</th></tr>
        <tr><td>i7-8700</td><td>6</td><td>4.6</td><td>4.6</td></tr>
        <tr><td colspan="4">Caption row 9</td></tr>
        </table>"""
    )
    assert [row["model"] for row in rows] == ["i7-8700"]
    assert rows[0]["boost_clock"] == "4.6"


def test_hidden_empty_rows_do_not_shift_rowspans() -> None:
    rows = _rows(
        """<table>
        <tr><th>Model</th><th>Cores</th><th>TDP (W)</th></tr>
        <tr><td>i5-8600</td><td rowspan="2">6</td><td>65</td></tr>
        <tr class="mw-empty-elt"></tr>
        <tr><td>i5-8600T</td><td>35</td></tr>
        </table>"""
    )
    assert [(row["model"], row["cores"], row["tdp"]) for row in rows] == [
        ("i5-8600", "6", "65 W"),
        ("i5-8600T", "6", "35 W"),
    ]


_RYZEN_HTML = """
<h3>{heading}</h3>
<table class="wikitable">{caption}
<tr><th>Model</th><th>Cores</th><th>Base (GHz)</th><th>Boost (GHz)</th>
<th>L3 (MiB)</th><th>TDP</th><th>Release date</th><th>Socket</th>{extra_th}</tr>
{rows}
</table>
"""


def _ryzen(
    rows: str,
    *,
    heading: str = "Vermeer (Zen 3)",
    caption: str = "",
    extra_th: str = "",
    manufacturer: str = "amd",
) -> dict[str, IngestCandidate]:
    html = _RYZEN_HTML.format(heading=heading, caption=caption, extra_th=extra_th, rows=rows)
    return _extract(html, manufacturer)


def test_range_tdp_and_rough_dates_are_left_empty() -> None:
    chips = _ryzen(
        "<tr><td>Ryzen 7 5800U</td><td>8</td><td>1.9</td><td>4.4</td><td>16</td>"
        "<td>10–25 W</td><td>2021</td><td>FP6</td></tr>"
        "<tr><td>Ryzen 7 5800H</td><td>8</td><td>3.2</td><td>4.4</td><td>16</td>"
        "<td>45 W (35–54 W)</td><td>1H 2021</td><td>Socket FP6</td></tr>"
        "<tr><td>Ryzen 5 5600H</td><td>6</td><td>3.3</td><td>4.2</td><td>16</td>"
        "<td>45 W</td><td>Q1 2021</td><td>FP6</td></tr>"
    )
    u, h, q = chips["ryzen-7-5800u"], chips["ryzen-7-5800h"], chips["ryzen-5-5600h"]
    assert u.record["tdp_w"] is None
    assert h.record["tdp_w"] == 45
    assert u.record["release_date"] is None
    assert h.record["release_date"] is None
    # "1H 2021" still tells the run date check when the part had shipped.
    assert str(h.released_by) == "2021-06-30"
    assert q.record["release_date"] == "2021-01-01"
    assert h.record["socket"] == "FP6"
    assert all(c.record.get("max_tdp_w") is None for c in chips.values())


def test_ryzen_x_parts_are_desktop_and_mobile_headings_mean_laptop() -> None:
    desktop = _ryzen(
        "<tr><td>Ryzen 7 5800X</td><td>8</td><td>3.8</td><td>4.7</td><td>32</td>"
        "<td>105 W</td><td>November 5, 2020</td><td>AM4</td></tr>"
    )
    assert desktop["ryzen-7-5800x"].record["segment"] == "desktop"
    for heading in ("Mobile processors", "Handheld", "Laptop", "Notebook"):
        handheld = _ryzen(
            "<tr><td>Ryzen Z2 Extreme</td><td>8</td><td>2.0</td><td>5.0</td><td>16</td>"
            "<td>28 W</td><td>January 6, 2025</td><td>FT6</td></tr>",
            heading=heading,
        )
        assert handheld["ryzen-z2-extreme"].record["segment"] == "laptop"


def test_assured_power_tables_leave_tdp_empty() -> None:
    chips = _ryzen(
        "<tr><td>Core Ultra 7 258V</td><td>8</td><td>2.2</td><td>4.8</td><td>12</td>"
        "<td>30 W</td><td>September 24, 2024</td><td>BGA 2833</td></tr>",
        heading="Lunar Lake",
        caption="<caption>Lunar Lake processors</caption>",
        manufacturer="intel",
    )
    assert chips["core-ultra-7-258v"].record["tdp_w"] is None


def test_pro_cells_give_each_variant_its_own_value() -> None:
    chips = _ryzen(
        "<tr><td>Ryzen 5 ( PRO ) 225</td><td>4</td><td>(3.3) 3.2</td><td>4.6</td><td>8</td>"
        "<td>65 W</td><td>(March 27, 2025) February 18, 2025</td><td>AM5</td></tr>"
    )
    plain, pro = chips["ryzen-5-225"], chips["ryzen-5-pro-225"]
    assert plain.record["base_clock_ghz"] == 3.2
    assert pro.record["base_clock_ghz"] == 3.3
    assert plain.record["release_date"] == "2025-02-18"
    assert pro.record["release_date"] == "2025-03-27"


def test_p_marker_nickname_and_codename_column() -> None:
    chips = _ryzen(
        '<tr><td>7643 (P)</td><td>48</td><td>2.3</td><td>3.6</td><td>256</td>'
        "<td>225 W</td><td>March 15, 2021</td><td>SP3</td><td></td></tr>"
        '<tr><td>9175F "Turin"</td><td>16</td><td>4.2</td><td>5.0</td><td>512</td>'
        "<td>320 W</td><td>October 10, 2024</td><td>SP5</td><td></td></tr>"
        "<tr><td>Ryzen Z2 A</td><td>4</td><td>2.8</td><td>3.8</td><td>4</td>"
        "<td>15 W</td><td>January 6, 2025</td><td>FT6</td><td>Van Gogh</td></tr>",
        extra_th="<th>Codename</th>",
    )
    assert {"ryzen-7643", "ryzen-7643p", "ryzen-9175f", "ryzen-z2-a"} <= set(chips)
    assert chips["ryzen-z2-a"].record["architecture"] == "Zen 2"


@pytest.mark.parametrize(
    "heading,expected",
    [
        ("Phoenix (Zen4/RDNA3 based)", "Zen 4"),
        ("Handheld", ""),
        ("Gaming", ""),
    ],
)
def test_architecture_from_heading_spellings(heading: str, expected: str) -> None:
    assert architecture_from_heading(heading) == expected


@pytest.mark.parametrize(
    "left,right",
    [
        ("Pentium Gold G7400", "Pentium G7400"),
        ("Pentium Silver N6000", "Pentium N6000"),
        ("Opteron X3216 APU", "Opteron X3216"),
        ("EPYC 7643 (P)", "EPYC 7643P"),
        ('EPYC 6275 "Fangio"', "EPYC 6275"),
    ],
)
def test_cpu_key_spellings(left: str, right: str) -> None:
    assert cpu_key(left, "amd") == cpu_key(right, "amd")


def test_missing_list_pages_are_not_fetched() -> None:
    assert "List_of_AMD_Threadripper_processors" not in {page for _, page, _ in PAGES}
