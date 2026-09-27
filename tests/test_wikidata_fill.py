"""Regression tests for cited-entity fill and conservative serialization."""
import json
from urllib.parse import parse_qs

import httpx
import pytest

from app.ingest.wikidata_fill import EntityCache, calendar_date, fill, run, serialize


def claim(value, rank="normal", snaktype="value"):
    return {"rank": rank, "mainsnak": {"snaktype": snaktype, "datavalue": {"value": value}}}


def timestamp(precision=11, time="+2020-02-29T00:00:00Z"):
    return {"precision": precision, "time": time,
            "calendarmodel": "http://www.wikidata.org/entity/Q1985727"}


def test_mapping_rank_and_no_overwrite():
    labels = {"Q2": {"id": "Q2", "labels": {"en": {"value": "English label"}}},
              "Q3": {"id": "Q3", "labels": {"fr": {"value": "French only"}}}}
    entity = {"id": "Q1", "claims": {
        p: [claim({"id": "Q3"}), claim({"id": "Q2"}, "preferred"),
            claim({"id": "Q3"}, "deprecated")] for p in
        ("P178", "P123", "P306", "P275", "P136", "P277", "P407", "P127")}}
    entity["claims"].update({"P577": [claim(timestamp())], "P571": [claim(timestamp())],
                              "P856": [claim("https://example.com")]})
    software = fill({"developers": ["Existing"], "publishers": None,
                     "licenses": [], "genres": "", "operating_systems": {}},
                    "software", entity, labels)
    assert software == {"developers": ["Existing"], "publishers": ["English label"],
                        "licenses": ["English label"], "genres": ["English label"],
                        "operating_systems": ["English label"], "release_date": "2020-02-29",
                        "programming_languages": ["English label"]}
    website = fill({}, "website", entity, labels)
    assert website == {"homepage_url": "https://example.com", "launch_date": "2020-02-29",
                       "languages": ["English label"], "owners": ["English label"]}
    assert fill(website, "website", entity, {}) == website


@pytest.mark.parametrize("precision", [0, 8, 9, 10, 12])
def test_precision_skips_inexpressible_dates(precision):
    assert calendar_date(timestamp(precision)) is None


def test_invalid_dates_calendar_and_unknown_values():
    assert calendar_date(timestamp(time="+2021-02-29T00:00:00Z")) is None
    assert calendar_date({**timestamp(), "calendarmodel": "Julian"}) is None
    assert calendar_date("2020") is None
    entity = {"claims": {"P577": [claim(timestamp(), "deprecated")],
                          "P178": [claim({}, snaktype="somevalue")],
                          "P856": [claim("javascript:alert(1)")]}}
    assert fill({}, "software", entity, {}) == {}
    assert fill({}, "website", entity, {}) == {}


def test_http_batches_cache_and_rate(tmp_path):
    requests = []
    sleeps = []

    def handler(request):
        params = parse_qs(request.url.query.decode())
        ids = params["ids"][0].split("|")
        requests.append(ids)
        assert params["maxlag"] == ["5"]
        assert params["languages"] == ["en"]
        assert "TechEngine" in request.headers["User-Agent"]
        assert "redirects" not in params
        return httpx.Response(200, json={"entities": {qid: {"id": qid} for qid in ids}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        cache = EntityCache(tmp_path, client, sleeps.append)
        ids = [f"Q{i}" for i in range(1, 52)]
        assert len(cache.fetch(ids + ids)) == 51
        assert [len(batch) for batch in requests] == [50, 1]
        assert len(sleeps) == 1 and 0 <= sleeps[0] <= 1
        assert len(EntityCache(tmp_path, client).fetch(ids)) == 51
        assert len(requests) == 2


def test_api_failure_is_not_cached(tmp_path):
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"error": {"code": "maxlag"}})
    )) as client:
        cache = EntityCache(tmp_path, client, lambda _: None)
        with pytest.raises(ValueError, match="incomplete"):
            cache.fetch(["Q1"])
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("entity", [{"id": "Q1", "redirect": "Q2"},
                                     {"id": "Q2"}, {"id": "Q1", "missing": ""}])
def test_redirect_and_missing_are_skipped(tmp_path, entity):
    root = tmp_path / "data"
    path = root / "website" / "site.json"
    path.parent.mkdir(parents=True)
    record = {"slug": "site", "name": "Site", "verified": False,
              "source_urls": ["https://www.wikidata.org/wiki/Q1"]}
    path.write_text(json.dumps(record), encoding="utf-8")
    entity["claims"] = {"P856": [claim("https://example.com")]}
    with httpx.Client(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"entities": {"Q1": entity}})
    )) as client:
        summary, samples = run("website", EntityCache(tmp_path / "cache", client), root)
    assert summary["changed_records"] == 0
    assert not samples
    assert json.loads(path.read_text()) == record


def test_dry_run_and_apply_serialization(tmp_path):
    path = tmp_path / "website" / "site.json"
    path.parent.mkdir()
    record = {"slug": "site", "name": "Site", "verified": False,
              "source_urls": ["https://www.wikidata.org/wiki/Q1"]}
    original = serialize(record, b"\r\n")
    path.write_bytes(original)
    entities = {"Q1": {"id": "Q1", "claims": {"P127": [claim({"id": "Q2"})]}},
                "Q2": {"id": "Q2", "labels": {"en": {"value": "Owner"}}}}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"entities": {qid: entities[qid] for qid in
                                request.url.params["ids"].split("|")}}
    ))) as client:
        cache = EntityCache(tmp_path / "cache", client, lambda _: None)
        summary, samples = run("website", cache, tmp_path)
        assert summary["fills"]["owners"] == 1 and len(samples) == 1
        assert path.read_bytes() == original
        run("website", cache, tmp_path, apply=True)
    rendered = path.read_bytes()
    assert rendered.endswith(b"\r\n") and b"\n" not in rendered.replace(b"\r\n", b"")
    assert list(json.loads(rendered)) == list(record) + ["owners"]
    assert b'  "owners": [' in rendered
    assert serialize(record, b'\xef\xbb\xbf\n').startswith(b'\xef\xbb\xbf')

def test_compact_cache_keeps_only_fill_properties(tmp_path):
    entity = {"id": "Q1", "claims": {
        "P178": [{**claim({"id": "Q2"}), "references": [{"huge": "unneeded"}]}],
        "P999999": [claim("unrelated")],
    }, "labels": {"en": {"value": "Name"}}, "sitelinks": {"enwiki": {"title": "Name"}}}
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(
        200, json={"entities": {"Q1": entity}}
    ))) as client:
        cached = EntityCache(tmp_path, client).fetch(["Q1"])["Q1"]
    assert set(cached["claims"]) == {"P178"}
    assert "references" not in cached["claims"]["P178"][0]
    assert "sitelinks" not in cached
    assert cached["labels"] == entity["labels"]
