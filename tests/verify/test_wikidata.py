import io
import json
from urllib.parse import parse_qs, urlparse

import pytest

from app.verify import http_check, promote, wikidata


@pytest.mark.parametrize("url", ["https://evil.org/wiki/Q1", "https://wikidata.org/wiki/Q0",
                                 "https://wikidata.org/wiki/Q1?x=1", "not a URL", None])
def test_malformed_qid_urls(url):
    assert wikidata.qid_of(url) is None


class Opener:
    def __init__(self, error=False):
        self.calls = []
        self.error = error

    def open(self, request, timeout):
        params = parse_qs(urlparse(request.full_url).query)
        self.calls.append((request, params))
        assert timeout == 30
        assert params["maxlag"] == ["5"]
        assert "github.com/GetTechAPI/TechEngine" in request.get_header("User-agent")
        ids = params["ids"][0].split("|")
        entities = {qid: {"id": qid, "lastrevid": 1} for qid in ids}
        if "Q2" in ids:
            entities["Q2"] = {"id": "Q2", "missing": ""}
        if "Q3" in ids:
            entities["Q3"] = {"id": "Q3", "redirect": "Q4"}
        payload = {"error": {"code": "maxlag"}} if self.error else {"entities": entities}
        return io.StringIO(json.dumps(payload))


def test_batches_dedupe_and_cache_promotion(tmp_path):
    urls = [f"https://www.wikidata.org/wiki/Q{i}" for i in range(1, 52)]
    urls.append("https://wikidata.org/wiki/Q1")
    op = Opener()
    sleeps = []
    results = [r for batch in wikidata.check_batches(urls, opener=op, sleep=sleeps.append)
               for r in batch]
    assert [len(call[1]["ids"][0].split("|")) for call in op.calls] == [50, 1]
    assert sleeps == [1.0]
    assert len(results) == 52
    assert sum(r.alive for r in results) == 50
    cache = {r.url: http_check.result_to_entry(r, "2026-09-27T00:00:00Z") for r in results}
    path = tmp_path / "url_cache.jsonl"
    http_check.save_cache(cache, path)
    loaded = http_check.load_cache(path)
    for qid, expected in [(1, True), (2, False), (3, False)]:
        decision = promote.decide(band="green", source_urls=[urls[qid - 1]], url_cache=loaded,
                                  crossref_decision=None)
        assert decision.promote is expected


def test_api_errors_are_not_cached():
    assert list(wikidata.check_batches(["https://wikidata.org/wiki/Q1"],
                                     opener=Opener(error=True), sleep=lambda _: None)) == [[]]


def test_transport_failure_is_not_dead():
    class BrokenOpener:
        def open(self, request, timeout):
            raise OSError("offline")

    assert list(wikidata.check_batches(["https://wikidata.org/wiki/Q1"],
                                     opener=BrokenOpener(), sleep=lambda _: None)) == [[]]


def test_cli_skips_fresh_ids_before_cap(monkeypatch):
    from argparse import Namespace
    from datetime import UTC, datetime

    from app.verify import cli
    from app.verify.common import Record

    urls = [f"https://wikidata.org/wiki/Q{i}" for i in range(1, 4)]
    records = [Record("software", f"{i}.json", {"source_urls": [u]})
               for i, u in enumerate(urls)]
    cache = {urls[0]: {"checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                       "reason": "wikidata-entity"}}
    monkeypatch.setattr(cli, "load_all", lambda _: {"software": records})
    monkeypatch.setattr(http_check, "load_cache", lambda: cache)
    seen = []
    monkeypatch.setattr(wikidata, "check_batches", lambda targets: seen.append(targets) or [])
    assert cli.cmd_check_wikidata(Namespace(category=None, recheck=False, ttl_days=30, max=1)) == 0
    assert seen == [[urls[1]]]
