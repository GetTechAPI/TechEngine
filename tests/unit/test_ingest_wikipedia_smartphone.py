"""Wikipedia smartphone list-page row extractor (offline — vendored HTML)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.ingest.sources.wikipedia_smartphone import WikipediaSmartphoneIngest

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
    assert ultra.output_path.as_posix() == "smartphone/samsung/galaxy-s25-ultra.json"


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
