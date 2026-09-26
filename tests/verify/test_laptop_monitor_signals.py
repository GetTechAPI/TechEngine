"""Plausibility boundaries distinguish outliers, missing data and impossibilities."""

import pytest

from app.verify.signals import signals_for


@pytest.mark.parametrize("category,record,name,hard", [
    ("laptop", {"ram_gb": 512}, "ram_plausible", False),
    ("laptop", {"weight_g": 300}, "weight_plausible", False),
    ("laptop", {"storage_gb": -1}, "storage_plausible", True),
    ("laptop", {"display": {"size_inch": float("inf")}}, "display_size_plausible", True),
    ("monitor", {"refresh_hz": 700}, "refresh_plausible", False),
    ("monitor", {"size_inch": -1}, "display_size_plausible", True),
    ("monitor", {"resolution": "unknown"}, "resolution_parses", False),
    ("monitor", {"resolution": "00x1080"}, "resolution_parses", True),
    ("monitor", {"refresh_hz": True}, "refresh_plausible", False),
    ("laptop", {"release_date": "2027-01-01"}, "release_not_future", False),
    ("monitor", {"release_date": "2027-01-01"}, "release_not_future", False),
    ("monitor", {"size_inch": 24, "resolution": "1920x1080", "ppi": 300},
     "ppi_consistent", False),
])
def test_outliers_are_soft_and_impossible_measurements_are_hard(category, record, name, hard):
    signal = next(s for s in signals_for(category, record, 2026, {}) if s.name == name)
    assert signal.failed
    assert signal.hard is hard


@pytest.mark.parametrize("size", [7, 86])
def test_specialty_monitor_sizes_are_plausible(size):
    assert not any(s.failed for s in signals_for("monitor", {"size_inch": size}, 2026, {}))


@pytest.mark.parametrize("category", ["laptop", "monitor"])
def test_absent_measurements_are_not_flags(category):
    assert all(s.result == "na" for s in signals_for(category, {}, 2026, {}))
