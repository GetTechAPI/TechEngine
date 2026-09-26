"""Tier 0 scorer + host classification tests."""

import pytest

from app import validate
from app.verify import hosts, offline
from app.verify.common import Record

NOW = 2026
NO_SOC: dict[str, str] = {}


@pytest.mark.parametrize("category,slug,fields,url,expected", [
    ("laptop", "acer-chromebook-14-ammok-691",
     {"ram_gb": 4, "os": "Chrome OS"},
     "https://huggingface.co/datasets/Ammok/laptop_price_prediction", 53.8),
    ("monitor", "acer-nitro-monitor-amazonmon-636",
     {"size_inch": 23.8, "resolution": "1920x1080"},
     "https://www.kaggle.com/datasets/durjoychandrapaul/amazon-products-sales-monitor-dataset",
     58.5),
])
def test_structurally_valid_variants_are_yellow(category, slug, fields, url, expected):
    base = "acer-chromebook-14" if category == "laptop" else "acer-nitro-monitor"
    data = {
        "slug": slug, "base_model_slug": base, "name": "Example", "brand": "acer",
        "release_date": "2023-01-01", "verified": False, "source_urls": [url], **fields,
    }
    path = f"{category}/acer/2023/{base}/{slug}.json"
    required = getattr(validate, f"{category.upper()}_REQUIRED")
    errors = []
    validate._check_required(path, data, required, errors)
    validate._check_slug(path, slug, errors)
    validate._check_source_urls(path, data, errors)
    validate._check_variant_path(path, data, category, errors, allow_flat=True)
    assert errors == []

    def score():
        return offline.score_record(Record(category, path, data), NOW, NO_SOC)

    result = score()
    assert result.band == "yellow"
    assert result.score == expected
    assert result.subscores["consistency"] == 0
    assert result.flags == ["domain_rules_unavailable"]

    # Strong sources can increase the normalized score, but cannot earn green
    # without domain consistency rules.
    data["source_urls"] = ["https://intel.com/example", "https://en.wikipedia.org/wiki/x"]
    assert score().score >= offline.GREEN_MIN
    assert score().band == "yellow"

    del data[next(iter(fields))]
    broken = score()
    assert broken.score >= offline.RED_MAX  # hard fail, despite a good numeric score
    assert broken.band == "red"
    assert "!structural_integrity" in broken.flags


def _score(category, data):
    return offline.score_record(Record(category, f"{category}/x.json", data), NOW, NO_SOC)


def test_host_tiers():
    assert hosts.tier_of_host("en.wikipedia.org") == 1
    assert hosts.tier_of_host("ark.intel.com") == 1  # subdomain of intel.com
    assert hosts.tier_of_host("gsmarena.com") == 2
    assert hosts.tier_of_host("www.wikidata.org") == 2
    assert hosts.tier_of_host("www.kaggle.com") == 3
    assert hosts.tier_of_host("example.org") == 0
    assert hosts.best_tier(["https://kaggle.com/x", "https://en.wikipedia.org/y"]) == 1


def test_wikidata_item_is_a_tier_two_source_for_a_complete_brand():
    rec = {
        "slug": "google", "founded_year": 1998,
        "description_en": "American technology company.",
        "source_urls": ["https://www.wikidata.org/wiki/Q95"],
    }
    score = _score("brand", rec)
    assert score.best_tier == 2
    assert score.band == "green"
    assert score.subscores["host"] == 18.0


def test_complete_authoritative_cpu_is_green():
    rec = {
        "slug": "core-i9-14900k", "cores": 24, "threads": 32,
        "base_clock_ghz": 3.2, "boost_clock_ghz": 6.0, "l3_cache_mb": 36,
        "socket": "LGA1700", "tdp_w": 125, "passmark_cpu_mark": 60000,
        "architecture": "Raptor Lake", "release_date": "2023-10-17",
        "source_urls": ["https://ark.intel.com/x", "https://en.wikipedia.org/wiki/x"],
    }
    s = _score("cpu", rec)
    assert s.band == "green"
    assert s.best_tier == 1


def test_hard_violation_forces_red_despite_good_source():
    rec = {
        "slug": "bad", "cores": 16, "threads": 8,  # threads < cores -> hard
        "base_clock_ghz": 3.0, "boost_clock_ghz": 4.0, "release_date": "2023-01-01",
        "architecture": "x", "socket": "y", "tdp_w": 65, "l3_cache_mb": 8,
        "passmark_cpu_mark": 20000,
        "source_urls": ["https://en.wikipedia.org/wiki/x"],
    }
    s = _score("cpu", rec)
    assert s.band == "red"
    assert "!threads_ge_cores" in s.flags


def test_kaggle_only_sparse_is_not_green():
    rec = {
        "slug": "sgh-x", "name": "SGH-X", "release_date": "2016-01-01",
        "display": {"type": "Alphanumeric"},
        "source_urls": ["https://www.kaggle.com/datasets/msainani/gsmarena-mobile-devices"],
    }
    s = _score("smartphone", rec)
    assert s.band != "green"  # T3-only source can never auto-green
    assert s.best_tier == 3


def test_future_release_red():
    rec = {
        "slug": "ghost", "cores": 8, "threads": 16, "release_date": "2099-01-01",
        "source_urls": ["https://en.wikipedia.org/wiki/x"],
    }
    assert _score("cpu", rec).band == "red"


def test_model_key_collapses_variants_of_one_phone():
    """Regional/RAM SKUs of one phone are one product, not many."""
    from app.verify.common import Record

    def variant(slug, base):
        return Record("smartphone", f"smartphone/lg/2020/{base}/{slug}.json",
                      {"slug": slug, "brand": "lg", "base_model_slug": base})

    a = variant("lg-k61-costa-rica-4gb-128gb", "k61-2020")
    b = variant("lg-k61-colombia-3gb-64gb", "k61-2020")
    other = variant("lg-k51-usa-3gb-32gb", "k51-2020")
    assert a.model_key == b.model_key
    assert a.model_key != other.model_key


def test_model_key_of_a_standalone_record_is_itself():
    from app.verify.common import Record

    rec = Record("cpu", "cpu/intel/2023/desktop/core-i9-14900k.json",
                 {"slug": "core-i9-14900k"})
    assert rec.model_key == ("cpu", "core-i9-14900k")
