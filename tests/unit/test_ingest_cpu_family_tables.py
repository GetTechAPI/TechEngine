"""Offline regression fixtures for family/SKU CPU tables and identity checks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.ingest.cpu_audit import _coverage_audit
from app.ingest.cpu_audit import main as audit_main
from app.ingest.cpu_identity import cpu_key, same_cpu
from app.ingest.pipeline import run
from app.ingest.sources.cpu_tables import parse_cpu_table
from app.ingest.sources.wikipedia_cpu import PAGES, WikipediaCpuIngest

HTML = """
<h4>Summit Ridge (Zen based)</h4>
<table class="wikitable">
<tr><th rowspan="2" colspan="2">Branding and Model</th>
<th rowspan="2">Cores (threads)</th><th colspan="2">CPU Clock (GHz)</th>
<th rowspan="2">L3 cache (MiB)</th><th rowspan="2">TDP (W)</th>
<th rowspan="2">Release date</th><th colspan="2">GPU</th></tr>
<tr><th>Base</th><th>Boost</th><th>Model</th><th>Clock (GHz)</th></tr>
<tr><th rowspan="2">Ryzen 7</th><td>1800X<sup>[2]</sup></td><td rowspan="2">8 (16)</td>
<td>3.6</td><td>4.0</td><td>16</td><td>95</td><td>March 2, 2017</td>
<td>Vega 11</td><td>1.2</td></tr>
<tr><td>1700X PRO</td><td>3.4</td><td>3.8</td><td>16</td><td>95</td>
<td>June 29, 2017</td><td>Vega 11</td><td>1.2</td></tr>
<tr><td colspan="10">A group caption 123</td></tr>
</table>
"""


def test_family_rowspan_and_stacked_cpu_headers() -> None:
    candidates = list(
        WikipediaCpuIngest._extract(HTML, "amd", "List_of_AMD_Ryzen_processors", "AMD Ryzen")
    )
    assert [c.slug for c in candidates] == ["ryzen-7-1800x", "ryzen-7-1700x-pro"]
    record = candidates[0].record
    assert record["name"] == "AMD Ryzen 7 1800X"
    assert record["cores"] == 8
    assert record["threads"] == 16
    assert record["base_clock_ghz"] == 3.6
    assert record["boost_clock_ghz"] == 4.0
    assert record["l3_cache_mb"] == 16
    assert record["tdp_w"] == 95
    assert record["verified"] is False
    assert record["source_urls"] == ["https://en.wikipedia.org/wiki/List_of_AMD_Ryzen_processors"]
    assert all(c.is_complete for c in candidates)
    assert candidates[1].record["threads"] == 16


def test_family_column_is_vendor_independent() -> None:
    html = """<table><tr><th>Processor family</th><th>Model</th><th>Cores</th></tr>
    <tr><td rowspan="2">Acme 5</td><td>A100</td><td>8 (2 CCX)</td></tr>
    <tr><td>A200</td><td>16</td></tr></table>"""
    table = BeautifulSoup(html, "html.parser").find("table")
    assert list(parse_cpu_table(table)) == [
        {"family": "Acme 5", "model": "A100", "cores": "8"},
        {"family": "Acme 5", "model": "A200", "cores": "16"},
    ]


def test_th_only_sku_row_inherits_all_other_cells() -> None:
    html = """<table><tr><th colspan="2">Branding and Model</th><th>Cores (threads)</th>
    <th>Released</th></tr><tr><th rowspan="2">Acme 7</th><th>A100H</th>
    <td rowspan="2">8 (16)</td><td rowspan="2">2024</td></tr>
    <tr><th>A100HS</th></tr></table>"""
    table = BeautifulSoup(html, "html.parser").find("table")
    rows = list(parse_cpu_table(table))
    assert [row["model"] for row in rows] == ["A100H", "A100HS"]
    assert rows[1]["family"] == "Acme 7"
    assert rows[1]["cores"] == "8 / 16"
    assert rows[1]["release_date"] == "2024"


def test_opteron_bare_cores_do_not_imply_threads() -> None:
    html = """<h3>Interlagos</h3><table class="wikitable">
    <tr><th>Model number</th><th>Cores</th><th colspan="2">Frequency (GHz)</th>
    <th>TDP (W)</th><th>Released</th></tr><tr><th>Model number</th><th>Cores</th>
    <th>Base</th><th>Turbo</th><th>TDP (W)</th><th>Released</th></tr>
    <tr><td colspan="6">B2, Quad core</td></tr>
    <tr><td>6204</td><td>4</td><td>3.3</td><td>Unknown</td><td>115</td>
    <td>November 14, 2011</td></tr></table>"""
    (candidate,) = WikipediaCpuIngest._extract(
        html, "amd", "List_of_AMD_Opteron_processors", "AMD Opteron"
    )
    assert candidate.slug == "opteron-6204"
    assert candidate.record["segment"] == "server"
    assert candidate.record["cores"] == 4
    assert candidate.record["threads"] is None
    assert candidate.record["boost_clock_ghz"] is None
    assert candidate.missing_fields == ("threads",)


def test_optional_pro_rows_expand_into_two_explicit_variants() -> None:
    html = HTML.replace("1800X<sup>[2]</sup>", "( PRO ) 1800X")
    candidates = list(WikipediaCpuIngest._extract(html, "amd", "test", "AMD Ryzen"))
    assert [c.slug for c in candidates[:2]] == ["ryzen-7-1800x", "ryzen-7-pro-1800x"]


def test_no_architecture_is_invented_from_page_family() -> None:
    candidates = list(
        WikipediaCpuIngest._extract(
            HTML.replace("<h4>Summit Ridge (Zen based)</h4>", ""), "amd", "test", "AMD Ryzen"
        )
    )
    assert candidates[0].record["architecture"] == ""
    assert "architecture" in candidates[0].missing_fields


@pytest.mark.parametrize(
    "left,right",
    [
        ("1800X", "AMD Ryzen 7 1800-X"),
        ("AMD Ryzen 7 PRO 1700X", "ryzen-7-1700x-pro"),
        ("AMD Opteron 1210 HE", "opteron1210he"),
    ],
)
def test_cpu_identity_is_symmetric_and_ignores_punctuation(left: str, right: str) -> None:
    a, b = cpu_key(left, "amd"), cpu_key(right, "amd")
    assert same_cpu(a, b)
    assert same_cpu(b, a)


@pytest.mark.parametrize(
    "left,right",
    [
        ("1200", "41200"),
        ("1800X", "1800"),
        ("1210", "1210HE"),
        ("1700X", "1700X PRO"),
        ("5700U", "5700G"),
    ],
)
def test_cpu_identity_preserves_variant_suffixes(left: str, right: str) -> None:
    assert not same_cpu(cpu_key(left, "amd"), cpu_key(right, "amd"))


def test_pipeline_dedups_against_target_root_and_within_run(tmp_path: Path) -> None:
    existing = tmp_path / "cpu" / "amd" / "existing.json"
    existing.parent.mkdir(parents=True)
    existing.write_text(json.dumps({"slug": "amd-ryzen-7-pro-1700x"}), encoding="utf-8")
    candidates = list(WikipediaCpuIngest._extract(HTML, "amd", "test", "AMD Ryzen"))
    result = run(candidates + candidates, data_root=tmp_path, dry_run=True)
    assert [c.slug for c in result.written] == ["ryzen-7-1800x"]
    assert len(result.skipped_existing) == 3
    assert list(tmp_path.rglob("*.json")) == [existing]


def test_opteron_is_wired_into_normal_cpu_ingest() -> None:
    assert ("amd", "List_of_AMD_Opteron_processors", "AMD Opteron") in PAGES


def test_audit_writes_only_the_artifact(tmp_path: Path) -> None:
    html_dir = tmp_path / "html"
    html_dir.mkdir()
    (html_dir / "List_of_AMD_Ryzen_processors.html").write_text(HTML, encoding="utf-8")
    data_root = tmp_path / "data"
    data_root.mkdir()
    output = tmp_path / "audit.json"
    assert (
        audit_main(
            [
                "--page",
                "List_of_AMD_Ryzen_processors",
                "--html-dir",
                str(html_dir),
                "--data-root",
                str(data_root),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    report = json.loads(output.read_text(encoding="utf-8"))
    assert report["counts"]["would_add"] == 2
    assert report["would_add"][0]["record"]["verified"] is False
    assert not list(data_root.rglob("*.json"))


def test_audit_separates_raw_coverage_false_positives_from_requested_pages() -> None:
    page = "List_of_AMD_Ryzen_processors"
    candidates = list(WikipediaCpuIngest._extract(HTML, "amd", page, "AMD Ryzen"))
    other = """<table class="wikitable"><tr><th>Model</th></tr>
    <tr><td>1210</td></tr></table>"""
    result = _coverage_audit(
        {page: HTML, "List_of_AMD_Opteron_processors": other},
        candidates,
        {"ryzen-7-1800x"},
        {"ryzen-7-1700x-pro"},
    )
    assert result["total"] == 4
    assert result["counts"] == {
        "non_model_or_unparsed_cell": 2,
        "already_curated": 1,
        "outside_requested_pages": 1,
    }
