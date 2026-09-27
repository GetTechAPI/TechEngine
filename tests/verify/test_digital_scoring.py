import pytest

from app.verify.common import Record
from app.verify.offline import score_record
from app.verify.signals import signals_for


@pytest.mark.parametrize("category,fields", [
    ("software", {"release_date": "2020-01-01", "developers": ["Developer"],
                  "operating_systems": ["Linux"], "licenses": ["MIT"], "genres": ["Editor"],
                  "programming_languages": ["C"], "publishers": ["Publisher"]}),
    ("website", {"homepage_url": "https://example.org", "launch_date": "2000-01-01",
                 "languages": ["English"], "owners": ["Owner"]}),
])
def test_rich_digital_record_is_green(category, fields):
    data = {**fields, "source_urls": ["https://www.wikidata.org/wiki/Q1"]}
    score = score_record(Record(category, "example.json", data), 2026, {})
    assert score.band == "green"
    assert score.flags == []


@pytest.mark.parametrize("category,field,value", [
    ("software", "release_date", "2020-02-30"),
    ("website", "launch_date", "0000-01-01"),
])
def test_impossible_dates_force_red(category, field, value):
    data = {field: value, "source_urls": ["https://www.wikidata.org/wiki/Q1"]}
    score = score_record(Record(category, "example.json", data), 2026, {})
    assert score.band == "red"
    assert f"!{field}_plausible" in score.flags


@pytest.mark.parametrize("category", ["software", "website"])
def test_missing_qid_is_soft_failure(category):
    sigs = signals_for(category, {}, 2026, {})
    assert sigs[0].failed and not sigs[0].hard
    assert all(s.result == "na" for s in sigs[1:])


@pytest.mark.parametrize("value", [[], "MIT", [""], [42]])
def test_invalid_software_lists_are_soft(value):
    sig = next(s for s in signals_for("software", {"licenses": value}, 2026, {})
               if s.name == "licenses_string_list")
    assert sig.failed and not sig.hard


def test_future_dates_and_bad_homepage_are_soft():
    sigs = signals_for("website", {"launch_date": "2099-01-01",
                                   "homepage_url": "https:///broken"}, 2026, {})
    assert all(not s.hard for s in sigs)
    assert sum(s.failed for s in sigs) == 3


@pytest.mark.parametrize("category,field,value", [
    ("software", "release_date", "1949-01-01"),
    ("website", "launch_date", "1962-01-01"),
])
def test_early_dates_are_ambiguous_not_impossible(category, field, value):
    sig = next(s for s in signals_for(category, {field: value}, 2026, {})
               if s.name == f"{field}_plausible")
    assert sig.failed and not sig.hard
