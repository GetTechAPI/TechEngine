"""Wikipedia smartphone list-page row extractor (offline — vendored HTML)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest.sources.wikipedia_smartphone import PAGES, WikipediaSmartphoneIngest

_HTML = """
<html><body>
<h3>Galaxy S25 series</h3>
<table class="wikitable">
  <tr>
    <th>Model</th>
    <th>Released</th>
    <th>SoC</th>
    <th>RAM</th>
    <th>Battery</th>
    <th>Weight</th>
    <th>OS</th>
  </tr>
  <tr>
    <td>Galaxy S25 Ultra</td>
    <td>February 7, 2025</td>
    <td>Qualcomm Snapdragon 8 Elite for Galaxy</td>
    <td>12 GB</td>
    <td>5,000 mAh</td>
    <td>218 g</td>
    <td>Android 15, One UI 7</td>
  </tr>
  <tr>
    <td>Galaxy S25</td>
    <td>February 7, 2025</td>
    <td>Qualcomm Snapdragon 8 Elite for Galaxy</td>
    <td>12 GB</td>
    <td>4,000 mAh</td>
    <td>162 g</td>
    <td>Android 15, One UI 7</td>
  </tr>
</table>
</body></html>
"""


def test_extracts_phone_with_known_soc(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("TECHAPI_DATA_DIR", str(tmp_path))
    # Seed a curated SoC matching what the test row references.
    soc_dir = tmp_path / "soc" / "qualcomm"
    soc_dir.mkdir(parents=True)
    (soc_dir / "snapdragon-8-elite.json").write_text(
        json.dumps({"slug": "snapdragon-8-elite"}), encoding="utf-8"
    )

    candidates = list(
        WikipediaSmartphoneIngest._extract(
            _HTML,
            "samsung",
            "List_of_Samsung_Galaxy_smartphones",
            known_socs={"snapdragon-8-elite"},
        )
    )
    by_slug = {c.slug: c for c in candidates}
    assert "galaxy-s25-ultra" in by_slug
    ultra = by_slug["galaxy-s25-ultra"]
    assert ultra.record["soc"] == "snapdragon-8-elite"
    assert ultra.record["brand"] == "samsung"
    assert ultra.record["ram_gb"] == 12
    assert ultra.record["battery_mah"] == 5000
    assert ultra.record["weight_g"] == 218
    assert ultra.record["release_date"] == "2025-02-07"
    assert ultra.record["os"] == "Android 15"
    assert ultra.record["verified"] is False
    assert ultra.is_complete
    assert ultra.output_path.as_posix() == "smartphone/samsung/2025/galaxy-s25-ultra.json"


def test_unknown_soc_renders_candidate_incomplete() -> None:
    candidates = list(
        WikipediaSmartphoneIngest._extract(
            _HTML,
            "samsung",
            "List_of_Samsung_Galaxy_smartphones",
            known_socs=set(),  # no curated SoC matches
        )
    )
    by_slug = {c.slug: c for c in candidates}
    ultra = by_slug["galaxy-s25-ultra"]
    assert ultra.record["soc"] is None
    assert "soc" in ultra.missing_fields
    assert not ultra.is_complete


def _phones(html: str) -> dict[str, object]:
    candidates = WikipediaSmartphoneIngest._extract(
        html, "apple", "List_of_iPhone_models", known_socs=set()
    )
    return {c.slug: c for c in candidates}


_IPHONE_HTML = """
<table class="wikitable">
  <tr><th rowspan="2">Model</th><th colspan="2">Release</th><th rowspan="2">RAM</th></tr>
  <tr><th>With OS</th><th>Date</th></tr>
  <tr><td>iPhone 5s [ 12 ]</td><td>iOS 7</td><td>September 20, 2013</td><td>1 GB</td></tr>
  <tr><td>iPhone 13 Pro / 13 Pro Max</td><td>iOS 15</td><td>September 24, 2021</td>
      <td>6 GB</td></tr>
  <tr><td>iPhone 5 iPhone 5c</td><td>iOS 6</td><td>September 21, 2012</td><td>1 GB</td></tr>
  <tr><td>iPhone 2027 lineup</td><td></td><td></td><td></td></tr>
  <tr><td>iPhone SE (2nd gen)</td><td>iOS 13</td><td>2020</td><td>3 GB</td></tr>
</table>
"""


def test_two_row_release_header_reads_the_date_leaf() -> None:
    phones = _phones(_IPHONE_HTML)
    five_s = phones["iphone-5s"]
    assert five_s.record["release_date"] == "2013-09-20"  # type: ignore[attr-defined]
    assert five_s.output_path.as_posix() == (  # type: ignore[attr-defined]
        "smartphone/apple/2013/iphone-5s.json"
    )


def test_rows_naming_two_phones_or_no_spec_are_dropped() -> None:
    phones = _phones(_IPHONE_HTML)
    assert set(phones) == {"iphone-5s", "iphone-se-2nd-gen"}


def test_phone_without_a_release_date_is_never_written() -> None:
    se = _phones(_IPHONE_HTML)["iphone-se-2nd-gen"]
    assert se.record["release_date"] is None  # type: ignore[attr-defined]
    assert se.problems == ("no release date to file it under",)  # type: ignore[attr-defined]


def test_missing_list_pages_are_not_fetched() -> None:
    assert "List_of_Xiaomi_smartphones" not in {page for _, page in PAGES}
