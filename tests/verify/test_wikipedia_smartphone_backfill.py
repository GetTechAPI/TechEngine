"""Offline unit tests for the Wikipedia smartphone backfill tool. No network."""

from __future__ import annotations

import json
from pathlib import Path

from app.verify.crossref import _heading_matches
from app.verify.wikipedia_smartphone_backfill import (
    WikiRow,
    backfill,
    decide,
    rows_from_html,
    sample_diverse,
    split_phone_name,
    variant_conflict,
    variant_marks,
)

_SAMPLE_HTML = """
<html><head><title>List of Samsung Galaxy smartphones - Wikipedia</title></head><body>
<h3>Galaxy S series</h3>
<table class="wikitable">
  <tr>
    <th>Model</th><th>Released</th><th>RAM</th><th>Battery</th><th>Display</th><th>SoC</th>
  </tr>
  <tr>
    <td>Galaxy S20</td><td>March 6, 2020</td><td>8 GB</td>
    <td>4000 mAh</td><td>6.2" 1440x3200</td><td>Exynos 990</td>
  </tr>
  <tr>
    <td>Galaxy S20+</td><td>March 6, 2020</td><td>8 GB</td>
    <td>4500 mAh</td><td>6.7" 1440x3200</td><td>Exynos 990</td>
  </tr>
  <tr>
    <td>Galaxy S20 Ultra</td><td>March 6, 2020</td><td>12 GB</td>
    <td>5000 mAh</td><td>6.9" 1440x3200</td><td>Exynos 990</td>
  </tr>
</table>
</body></html>
"""


def _sample_rec(**overrides: object) -> dict:
    rec = {
        "slug": "samsung-galaxy-s20",
        "name": "Samsung Galaxy S20",
        "brand": "samsung",
        "release_date": "2020-03-06",
        "ram_gb": 8,
        "battery_mah": 4000,
        "weight_g": 163.0,
        "display": {
            "size_inch": 6.2,
            "resolution": "1440x3200",
        },
        "soc": "samsung-exynos-990",
        "os": "Android 10",
        "source_urls": ["https://www.kaggle.com/datasets/msainani/gsmarena-mobile-devices"],
    }
    rec.update(overrides)
    return rec


def test_variant_detection_and_conflict() -> None:
    assert "5g" in variant_marks("Galaxy A52 5G")
    assert "5g" not in variant_marks("Galaxy A52")
    assert variant_conflict("Galaxy A52", "Galaxy A52 5G")

    assert "pro" in variant_marks("iPhone 12 Pro")
    assert "pro" not in variant_marks("iPhone 12")
    assert variant_conflict("iPhone 12", "iPhone 12 Pro")

    assert "plus" in variant_marks("Galaxy S20+")
    assert "plus" in variant_marks("Galaxy S20 Plus")
    assert variant_conflict("Galaxy S20", "Galaxy S20+")

    assert "ultra" in variant_marks("Galaxy S20 Ultra")
    assert variant_conflict("Galaxy S20", "Galaxy S20 Ultra")

    assert "tablet" in variant_marks("Galaxy Tab S7")
    assert variant_conflict("Galaxy S7", "Galaxy Tab S7")

    assert not variant_conflict("Samsung Galaxy S20", "Galaxy S20")


def test_name_splitting_and_heading_match() -> None:
    split = split_phone_name("Samsung Galaxy S20 8GB 128GB")
    assert split.base == "Samsung Galaxy S20"
    assert _heading_matches(split.base, "Galaxy S20")

    scrape_name = split_phone_name("Galaxy S20-scrapegsma-1476")
    assert scrape_name.base == "Galaxy S20"


def test_year_alone_cannot_confirm() -> None:
    rec = _sample_rec(battery_mah=None, ram_gb=None, display=None, soc=None, weight_g=None, os=None)
    row = WikiRow(
        model="Galaxy S20",
        url="https://en.wikipedia.org/wiki/Samsung_Galaxy_S20",
        page="List_of_Samsung_Galaxy_smartphones",
        year=2020,
    )
    outcome = decide(rec, [row])
    assert outcome.decision == "ambiguous"
    assert outcome.reason == "insufficient-specs"


def test_confirm_requires_two_specs_and_rank() -> None:
    rec = _sample_rec()
    row = WikiRow(
        model="Galaxy S20",
        url="https://en.wikipedia.org/wiki/Samsung_Galaxy_S20",
        page="List_of_Samsung_Galaxy_smartphones",
        year=2020,
        battery_mah=4000,
        display_size_inch=6.2,
    )
    outcome = decide(rec, [row])
    assert outcome.decision == "confirm"
    assert "battery_mah" in outcome.agreements
    assert "display_size" in outcome.agreements
    assert outcome.proposed_url == "https://en.wikipedia.org/wiki/Samsung_Galaxy_S20"


def test_variant_conflict_blocks_confirm() -> None:
    rec = _sample_rec(name="Samsung Galaxy S20 5G")
    row = WikiRow(
        model="Galaxy S20",
        url="https://en.wikipedia.org/wiki/Samsung_Galaxy_S20",
        page="List_of_Samsung_Galaxy_smartphones",
        year=2020,
        battery_mah=4000,
    )
    outcome = decide(rec, [row])
    assert outcome.decision == "ambiguous"
    assert outcome.reason == "variant-conflict"


def test_spec_conflict_yields_contradict() -> None:
    rec = _sample_rec(battery_mah=5000)
    row = WikiRow(
        model="Galaxy S20",
        url="https://en.wikipedia.org/wiki/Samsung_Galaxy_S20",
        page="List_of_Samsung_Galaxy_smartphones",
        year=2020,
        battery_mah=3000,
    )
    outcome = decide(rec, [row])
    assert outcome.decision == "contradict"
    assert outcome.reason == "spec-conflict"


def test_multiple_rows_stays_ambiguous() -> None:
    rec = _sample_rec()
    row1 = WikiRow(
        model="Galaxy S20",
        url="https://en.wikipedia.org/wiki/Samsung_Galaxy_S20#VersionA",
        page="List_of_Samsung_Galaxy_smartphones",
        section="VersionA",
        year=2020,
        battery_mah=4000,
        ram_gb=(8.0,),
    )
    row2 = WikiRow(
        model="Galaxy S20",
        url="https://en.wikipedia.org/wiki/Samsung_Galaxy_S20#VersionB",
        page="List_of_Samsung_Galaxy_smartphones",
        section="VersionB",
        year=2020,
        battery_mah=4000,
        ram_gb=(8.0,),
    )
    outcome = decide(rec, [row1, row2])
    assert outcome.decision == "ambiguous"
    assert outcome.reason == "multiple-rows"


def test_rows_from_html_parsing() -> None:
    rows = rows_from_html(_SAMPLE_HTML, "List_of_Samsung_Galaxy_smartphones")
    models = {r.model for r in rows}
    assert "Galaxy S20" in models
    assert "Galaxy S20+" in models
    assert "Galaxy S20 Ultra" in models

    s20 = next(r for r in rows if r.model == "Galaxy S20")
    assert s20.year == 2020
    assert s20.battery_mah == 4000
    assert s20.display_size_inch == 6.2
    assert s20.display_resolution == "1440x3200"


def test_sample_diverse_distribution() -> None:
    items = [
        ("data/smartphone/samsung/2020/s1.json", {"brand": "samsung"}),
        ("data/smartphone/samsung/2020/s2.json", {"brand": "samsung"}),
        ("data/smartphone/apple/2020/a1.json", {"brand": "apple"}),
        ("data/smartphone/google/2020/g1.json", {"brand": "google"}),
    ]
    sampled = sample_diverse(items, limit=3)
    assert len(sampled) == 3
    brands = [rec["brand"] for _, rec in sampled]
    assert len(set(brands)) == 3


def test_dry_run_does_not_modify_files(tmp_path: Path) -> None:
    file_path = tmp_path / "test-phone.json"
    initial_content = json.dumps(_sample_rec(), indent=2)
    file_path.write_text(initial_content, encoding="utf-8")

    rec = json.loads(initial_content)
    result = backfill(
        tmp_path,
        records=[("test-phone.json", rec)],
        fetch_page=lambda p: (200, f"https://en.wikipedia.org/wiki/{p}", _SAMPLE_HTML),
        search_fn=lambda n: [],
        dry_run=True,
        apply=False,
    )
    assert result.written == 0
    assert file_path.read_text(encoding="utf-8") == initial_content


def test_apply_writes_only_confirmed_records(tmp_path: Path) -> None:
    file_path = tmp_path / "test-phone.json"
    initial_content = json.dumps(_sample_rec(), indent=2)
    file_path.write_text(initial_content, encoding="utf-8")

    rec = json.loads(initial_content)
    result = backfill(
        tmp_path,
        records=[("test-phone.json", rec)],
        fetch_page=lambda p: (200, f"https://en.wikipedia.org/wiki/{p}", _SAMPLE_HTML),
        search_fn=lambda n: [],
        dry_run=False,
        apply=True,
    )
    assert result.written == 1
    updated = json.loads(file_path.read_text(encoding="utf-8"))
    assert (
        "https://en.wikipedia.org/wiki/List_of_Samsung_Galaxy_smartphones#Galaxy_S_series"
        in updated["source_urls"]
    )
