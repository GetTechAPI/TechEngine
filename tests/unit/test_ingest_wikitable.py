"""Grid-aware table parser tests with rowspan + colspan fixtures."""

from __future__ import annotations

from bs4 import BeautifulSoup

from app.ingest.sources.wikitable import parse_table

RULES = {
    "model": ["model"],
    "architecture": ["architecture", "codename"],
    "cores": ["cores"],
    "tdp": ["tdp"],
}


def _table(html: str):
    return BeautifulSoup(html, "html.parser").find("table")


def test_rowspan_carries_architecture_down() -> None:
    html = """
    <table class="wikitable">
      <tr><th>Architecture</th><th>Model</th><th>Cores</th><th>TDP</th></tr>
      <tr><td rowspan="2">Raptor Lake</td><td>i9-13900K</td><td>24</td><td>125 W</td></tr>
      <tr><td>i7-13700K</td><td>16</td><td>125 W</td></tr>
      <tr><td>Alder Lake</td><td>i9-12900K</td><td>16</td><td>125 W</td></tr>
    </table>
    """
    rows = list(parse_table(_table(html), RULES))
    by_model = {r.cells["model"]: r for r in rows}
    assert by_model["i9-13900K"].cells["architecture"] == "Raptor Lake"
    assert by_model["i7-13700K"].cells["architecture"] == "Raptor Lake"
    assert by_model["i9-12900K"].cells["architecture"] == "Alder Lake"


def test_colspan_expands_a_single_cell() -> None:
    html = """
    <table class="wikitable">
      <tr><th colspan="2">Section</th><th>Model</th><th>Cores</th></tr>
      <tr><td>A</td><td>B</td><td>i9-test</td><td>8</td></tr>
    </table>
    """
    rows = list(parse_table(_table(html), {"model": ["model"], "cores": ["cores"]}))
    assert rows[0].cells["model"] == "i9-test"
    assert rows[0].cells["cores"] == "8"


def test_requires_model_column_in_headers() -> None:
    html = """
    <table class="wikitable">
      <tr><th>Color</th><th>Shape</th></tr>
      <tr><td>red</td><td>square</td></tr>
    </table>
    """
    assert list(parse_table(_table(html), {"model": ["model"]})) == []


def test_skips_empty_rows() -> None:
    html = """
    <table class="wikitable">
      <tr><th>Model</th><th>Cores</th></tr>
      <tr><td></td><td></td></tr>
      <tr><td>i9-13900K</td><td>24</td></tr>
    </table>
    """
    rules = {"model": ["model"], "cores": ["cores"]}
    rows = list(parse_table(_table(html), rules))
    models = [r.cells.get("model") for r in rows]
    assert models == ["i9-13900K"]
