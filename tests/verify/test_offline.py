"""Tier 0 scorer + host classification tests."""

import pytest

from app.verify import hosts, offline
from app.verify.common import Record

NOW = 2026
NO_SOC: dict[str, str] = {}


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


@pytest.mark.parametrize("category,fields", [
    ("laptop", {"cpu_name": "Example CPU", "ram_gb": 16, "storage_gb": 512,
                "display": {"size_inch": 14, "resolution": "1920x1080", "ppi": 157},
                "weight_g": 1400, "gpu_name": "Integrated", "os": "Linux"}),
    ("monitor", {"size_inch": 24, "resolution": "1920x1080", "refresh_hz": 144,
                 "panel_type": "IPS", "ppi": 92, "aspect_ratio": "16:9",
                 "features": {"ports": ["HDMI"], "response_time_ms": 1}}),
])
@pytest.mark.parametrize("url", ["https://intel.com/example", "https://wikidata.org/wiki/Q1"])
def test_complete_laptop_monitor_can_be_green(category, fields, url):
    data = {**fields, "release_date": "2023-01-01", "source_urls": [url]}
    score = _score(category, data)
    assert score.band == "green"
    assert score.subscores["consistency"] == 35
    assert score.flags == []
    # A physical impossibility overrides an otherwise rich, well-sourced record.
    data["ram_gb" if category == "laptop" else "size_inch"] = 0
    assert _score(category, data).band == "red"


@pytest.mark.parametrize("category", ["laptop", "monitor"])
def test_sparse_bulk_laptop_monitor_stays_yellow(category):
    data = {"release_date": "2023-01-01", "source_urls": ["https://kaggle.com/example"]}
    score = _score(category, data)
    assert score.band == "yellow"
    assert "domain_rules_unavailable" not in score.flags
    assert score.subscores["consistency"] == 35
