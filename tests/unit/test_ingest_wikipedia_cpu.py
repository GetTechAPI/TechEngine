"""Wikipedia CPU list-page row extractor (offline — vendored HTML)."""

from __future__ import annotations

from app.ingest.sources.wikipedia_cpu import WikipediaCpuIngest

# Realistic synthetic snippet: a section header followed by a wikitable whose
# headers match the recognized aliases.
_HTML = """
<html><body>
<h3>Raptor Lake (13th gen)</h3>
<table class="wikitable">
  <tr>
    <th>Model</th>
    <th>Cores</th>
    <th>Threads</th>
    <th>Base clock</th>
    <th>Turbo</th>
    <th>L3 cache</th>
    <th>TDP</th>
    <th>Released</th>
    <th>Socket</th>
    <th>Process</th>
  </tr>
  <tr>
    <td>Intel Core i9-13900K</td>
    <td>24</td>
    <td>32</td>
    <td>3.0 GHz</td>
    <td>5.8 GHz</td>
    <td>36 MB</td>
    <td>125 W</td>
    <td>October 20, 2022</td>
    <td>LGA1700</td>
    <td>Intel 7</td>
  </tr>
  <tr>
    <td>Intel Core i7-13700</td>
    <td>16</td>
    <td>24</td>
    <td>2.1 GHz</td>
    <td>5.2 GHz</td>
    <td>30 MB</td>
    <td>65 W</td>
    <td>January 3, 2023</td>
    <td>LGA1700</td>
    <td>Intel 7</td>
  </tr>
  <tr>
    <td>Codename: Raptor Lake (header row)</td>
    <td colspan="9">---</td>
  </tr>
</table>
</body></html>
"""


def test_extracts_complete_rows_into_records() -> None:
    candidates = list(
        WikipediaCpuIngest._extract(_HTML, "intel", "List_of_Intel_Core_processors", "Intel Core")
    )
    by_slug = {c.slug: c for c in candidates}
    assert "core-i9-13900k" in by_slug
    assert "core-i7-13700" in by_slug
    flagship = by_slug["core-i9-13900k"]
    assert flagship.is_complete
    assert flagship.record["cores"] == 24
    assert flagship.record["threads"] == 32
    assert flagship.record["base_clock_ghz"] == 3.0
    assert flagship.record["boost_clock_ghz"] == 5.8
    assert flagship.record["l3_cache_mb"] == 36.0
    assert flagship.record["tdp_w"] == 125
    assert flagship.record["release_date"] == "2022-10-20"
    assert flagship.record["architecture"] == "Raptor Lake (13th gen)"
    assert flagship.record["socket"] == "LGA1700"
    assert flagship.record["process_node"] == "Intel 7"
    assert flagship.record["segment"] == "desktop"
    assert flagship.record["verified"] is False
    assert flagship.source_url.endswith("List_of_Intel_Core_processors")
    assert flagship.output_path.as_posix() == "cpu/intel/2022/consumer/core-i9-13900k.json"


def test_filters_non_model_rows_lacking_a_slug() -> None:
    candidates = list(
        WikipediaCpuIngest._extract(_HTML, "intel", "List_of_Intel_Core_processors", "Intel Core")
    )
    # The "Codename: ..." row has no digit in its model column → slug is too
    # short and gets filtered out.
    slugs = {c.slug for c in candidates}
    assert all("raptor" not in slug for slug in slugs)


# Atom-style table rendered from {{cpulist}}: an "L2 cache" column, a
# base–turbo range in one frequency cell, a fractional TDP, a family-tier
# group row and a footnoted model name.
_ATOM_HTML = """
<html><body>
<h3>" Avoton " (22 nm)</h3>
<table class="wikitable">
  <tr>
    <th>Model</th><th>Cores</th><th>Frequency</th><th>L2 cache</th>
    <th>TDP</th><th>Released</th><th>Socket</th>
  </tr>
  <tr>
    <td>Atom C2530</td><td>4</td><td>1.7–2.0 GHz</td><td>2 × 1 MB</td>
    <td>9 W</td><td>September 2013</td><td>FC-BGA 1283</td>
  </tr>
  <tr>
    <td>Atom C2508</td><td>4</td><td>1.25 GHz</td><td>2 × 1 MB</td>
    <td>9.5 W</td><td>March 2014</td><td>FC-BGA 1283</td>
  </tr>
  <tr>
    <td>Atom 3 [ 12 ]</td><td>4</td><td>1.0 GHz</td><td>1 MB</td>
    <td>5 W</td><td>2014</td><td>BGA</td>
  </tr>
  <tr>
    <td>Atom C2338 [ 7 ]</td><td>2</td><td>1.7 GHz</td><td>1 MB</td>
    <td>7 W</td><td>2014</td><td>BGA</td>
  </tr>
</table>
</body></html>
"""


def _atom() -> dict[str, dict[str, object]]:
    candidates = WikipediaCpuIngest._extract(
        _ATOM_HTML, "intel", "List_of_Intel_Atom_processors", "Intel Atom"
    )
    return {c.slug: c.record for c in candidates}


def test_l2_cache_column_is_not_written_as_l3() -> None:
    assert _atom()["atom-c2530"]["l3_cache_mb"] is None


def test_frequency_range_splits_into_base_and_boost() -> None:
    record = _atom()["atom-c2530"]
    assert record["base_clock_ghz"] == 1.7
    assert record["boost_clock_ghz"] == 2.0


def test_fractional_tdp_rounds_instead_of_dropping_the_integer_part() -> None:
    assert _atom()["atom-c2508"]["tdp_w"] == 10


def test_month_year_release_keeps_the_month() -> None:
    assert _atom()["atom-c2530"]["release_date"] == "2013-09-01"


def test_family_tier_rows_and_footnotes_are_handled() -> None:
    records = _atom()
    assert "atom-3" not in records
    assert "atom-c2338" in records
    assert records["atom-c2338"]["name"] == "Intel Atom C2338"


def test_quoted_heading_is_cleaned_and_node_extracted() -> None:
    record = _atom()["atom-c2530"]
    assert record["architecture"] == "Avoton"
    assert record["process_node"] == "22 nm"
