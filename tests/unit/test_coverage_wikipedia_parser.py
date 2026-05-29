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
