"""Wikipedia HTML parser unit test (offline; uses a vendored snippet)."""

from __future__ import annotations

from app.coverage.sources.wikipedia import wikitable_first_cells
from app.coverage.sources.wikipedia_cpu import WikipediaCpu

# Small synthetic snippet that mirrors the structure of a real Wikipedia
# ``List_of_*_processors`` page (table.wikitable + first-column model name).
_HTML = """
<html><body>
  <table class="wikitable">
    <tr><th>Model</th><th>Cores</th><th>Clock</th></tr>
    <tr><td>Intel Core i9-14900K</td><td>24</td><td>3.2</td></tr>
    <tr><td>Intel Core i7-14700K</td><td>20</td><td>3.4</td></tr>
    <tr><td>Codename: Raptor Lake</td><td colspan="2">(header row)</td></tr>
  </table>
  <table class="not-wikitable">
    <tr><td>Should-be-ignored</td></tr>
  </table>
</body></html>
"""


def test_wikitable_first_cells_only_reads_wikitable_class() -> None:
    cells = list(wikitable_first_cells(_HTML))
    assert "Should-be-ignored" not in cells
    assert "Intel Core i9-14900K" in cells


def test_wikipedia_cpu_extract_filters_obvious_non_models() -> None:
    points = list(WikipediaCpu._extract(_HTML, "intel", "List_of_Intel_Core_processors"))
    slugs = {p.slug for p in points}
    assert "core-i9-14900k" in slugs
    assert "core-i7-14700k" in slugs
    # "Codename: Raptor Lake" has no digit and should be filtered.
    assert all("raptor" not in s for s in slugs)


# Mirrors the AMD list pages that produced garbage slugs in the auto report:
# footnote markers (<sup> + leftover "[ c ]") and a decimal clock cell.
_HTML_NOISY = """
<html><body>
  <table class="wikitable">
    <tr><th>Model</th></tr>
    <tr><td>1200<sup class="reference">[4]</sup></td></tr>
    <tr><td>1200 (AF) [ 16 ] [ c ]</td></tr>
    <tr><td>1.25</td></tr>
    <tr><td>1210</td></tr>
  </table>
</body></html>
"""


def test_wikitable_first_cells_strips_footnote_markers() -> None:
    cells = list(wikitable_first_cells(_HTML_NOISY))
    # <sup> reference and bracketed leftover markers are gone.
    assert "1200" in cells
    assert all("[" not in c and "]" not in c for c in cells)


def test_wikipedia_cpu_extract_drops_decimal_and_footnote_artifacts() -> None:
    points = list(WikipediaCpu._extract(_HTML_NOISY, "amd", "List_of_AMD_Ryzen_processors"))
    slugs = {p.slug for p in points}
    # Footnote suffixes no longer appear.
    assert "1200-4" not in slugs
    assert "1200-af-16-c" not in slugs
    # Decimal clock cell "1.25" -> "1-25" is rejected as a non-model artifact.
    assert "1-25" not in slugs
    # A genuine bare-numeric SKU still survives.
    assert "1210" in slugs
